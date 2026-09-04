"""Targeted re-enrich of 'missed' filings (forensics from a non-target model)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.reenrich import (
    clear_field_backfill_targets,
    clear_missed_filings,
    select_field_backfill_targets,
    select_missed_filings,
)
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from tests.test_summarize import FakeEnricher, _install

TARGET = "claude-sonnet-5"
BODY = "Former employee insider data exfiltration via removable media, trade secret theft. " * 40


def _seed(
    tmp_path: Path,
    monkeypatch,
    forensics_model: str,
    *,
    source_id="courtlistener-recap",
    channel="filings",
):
    """One enriched row whose forensic record is stamped with forensics_model."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            RawArticle(
                title="Filing: data exfiltration case",
                link="https://ex.com/case",
                content=BODY,
                published=datetime(2026, 7, 1, tzinfo=UTC),
                source_id=source_id,
                source_name="Source",
                channel=channel,
            )
        ]
    )
    fake = FakeEnricher()
    fake.model_name = forensics_model
    _install(monkeypatch, fake)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    row = JsonlProcessedStore(processed_path).load_all()[0]
    assert row.forensics is not None and row.forensics.model == forensics_model
    return raw_path, processed_path


def test_selects_only_non_target_filings(tmp_path, monkeypatch) -> None:
    _, processed = _seed(tmp_path, monkeypatch, "claude-haiku-4-5")
    missed = select_missed_filings(processed, target_model=TARGET)
    assert missed == ["https://ex.com/case"]


def test_skips_rows_already_on_target(tmp_path, monkeypatch) -> None:
    _, processed = _seed(tmp_path, monkeypatch, TARGET)
    assert select_missed_filings(processed, target_model=TARGET) == []


def test_selects_stale_schema_even_on_target_model(tmp_path, monkeypatch) -> None:
    """A row on the target model but an older clamp generation is still missed."""
    _, processed = _seed(tmp_path, monkeypatch, TARGET)
    # Simulate a row enriched under an older (narrower-clamp) schema generation.
    store = JsonlProcessedStore(processed)
    rows = store.load_all()
    rows[0].forensics.schema_version = 1
    store.replace_all(rows)
    assert select_missed_filings(processed, target_model=TARGET) == ["https://ex.com/case"]


def test_ignores_non_filings(tmp_path, monkeypatch) -> None:
    _, processed = _seed(
        tmp_path, monkeypatch, "claude-haiku-4-5", source_id="example", channel="news"
    )
    assert select_missed_filings(processed, target_model=TARGET) == []


def test_clear_makes_missed_filing_reenrich(tmp_path, monkeypatch) -> None:
    raw_path, processed = _seed(tmp_path, monkeypatch, "claude-haiku-4-5")

    cleared = clear_missed_filings(processed, target_model=TARGET)
    assert cleared == 1
    # Cleared row now lacks forensics → a normal backfill candidate.
    assert JsonlProcessedStore(processed).load_all()[0].forensics is None

    # Next sweep re-enriches it on the (fake) target model.
    fresh = FakeEnricher()
    fresh.model_name = TARGET
    _install(monkeypatch, fresh)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    run_processing(raw_path=raw_path, processed_path=processed)
    row = JsonlProcessedStore(processed).load_all()[0]
    assert row.forensics is not None and row.forensics.model == TARGET
    # Now on target → no longer missed.
    assert select_missed_filings(processed, target_model=TARGET) == []


def test_reconcile_restores_when_reenrichment_regresses(tmp_path, monkeypatch) -> None:
    """Non-destructive: a floored re-enrichment must keep the prior rich record."""
    from tests.test_summarize import _reply

    # Seed a RICH record on a non-target model.
    raw_path, processed = _seed(tmp_path, monkeypatch, "claude-haiku-4-5")
    before = JsonlProcessedStore(processed).load_all()[0]
    assert before.ai_summary and before.forensics and before.forensics.methods

    # Re-enrich under the target, but the enricher now floors (empty reply) — as
    # if the docket's source text were too thin to ground a record.
    floor = FakeEnricher(
        reply=_reply(ai_summary="", is_insider_case=False, confidence=0.0, methods=[], outcome=None)
    )
    floor.model_name = TARGET
    _install(monkeypatch, floor)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    monkeypatch.setenv("SUMMARIZER_REENRICH_MISSED_LIMIT", "10")
    monkeypatch.setenv("SUMMARIZER_REENRICH_MODEL", TARGET)

    result = run_processing(raw_path=raw_path, processed_path=processed)
    assert result.reenrich_cleared == 1
    assert result.reenrich_restored == 1
    after = JsonlProcessedStore(processed).load_all()[0]
    # The rich prior record survived the floored re-enrichment — not gutted.
    assert after.ai_summary == before.ai_summary
    assert after.forensics is not None and after.forensics.methods
    assert after.forensics.model == "claude-haiku-4-5"


def test_env_gated_hook_clears_then_reenriches_in_one_run(tmp_path, monkeypatch) -> None:
    raw_path, processed = _seed(tmp_path, monkeypatch, "claude-haiku-4-5")

    fresh = FakeEnricher()
    fresh.model_name = TARGET
    _install(monkeypatch, fresh)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    monkeypatch.setenv("SUMMARIZER_REENRICH_MISSED_LIMIT", "10")
    monkeypatch.setenv("SUMMARIZER_REENRICH_MODEL", TARGET)

    result = run_processing(raw_path=raw_path, processed_path=processed)
    assert result.reenrich_cleared == 1
    row = JsonlProcessedStore(processed).load_all()[0]
    assert row.forensics is not None and row.forensics.model == TARGET


# --- additive-field backfill lane (docs/schema-freeze-v4.md) ----------------------


def _seed_rows(tmp_path: Path, monkeypatch, rows: list[dict]) -> Path:
    """Seed N enriched rows (verdict-true, v3) then patch each per ``rows``.

    Each dict: link, channel, source_id, published, plus optional overrides
    applied to the stored forensics (industry, sector, verdict, schema).
    """
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            RawArticle(
                title=f"Case {i}",
                link=r["link"],
                content=BODY,
                published=r.get("published", datetime(2026, 7, 1 + i, tzinfo=UTC)),
                source_id=r.get("source_id", "courtlistener-recap"),
                source_name="Source",
                channel=r.get("channel", "filings"),
            )
            for i, r in enumerate(rows)
        ]
    )
    fake = FakeEnricher()
    fake.model_name = TARGET
    _install(monkeypatch, fake)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "20")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    store = JsonlProcessedStore(processed_path)
    stored = store.load_all()
    by_link = {r["link"]: r for r in rows}
    for row in stored:
        assert row.forensics is not None, row.link
        spec = by_link[row.link]
        row.forensics.industry = spec.get("industry", "financial-services")
        row.forensics.actor_employer_sector = spec.get("sector")
        row.forensics.is_insider_case = spec.get("verdict", True)
        row.forensics.schema_version = spec.get("schema", 3)
    store.replace_all(stored)
    return processed_path


def test_field_backfill_gates(tmp_path, monkeypatch) -> None:
    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [
            {"link": "https://ex.com/fs-news", "channel": "news", "source_id": "example"},
            {"link": "https://ex.com/unknown", "industry": "unknown"},
            {"link": "https://ex.com/has-it", "sector": "professional-services"},
            {"link": "https://ex.com/non-case", "verdict": False},
            {"link": "https://ex.com/stale", "schema": 2},
            {"link": "https://ex.com/healthcare", "industry": "healthcare"},
        ],
    )
    targets = select_field_backfill_targets(processed, field="actor_employer_sector")
    links = [r.link for r in targets]
    # Newest first: /unknown published 07-02, /fs-news 07-01.
    assert links == ["https://ex.com/unknown", "https://ex.com/fs-news"]
    # Industry set is a parameter; None = any industry (healthcare joins).
    any_ind = select_field_backfill_targets(
        processed, field="actor_employer_sector", industries=None
    )
    assert {r.link for r in any_ind} == {
        "https://ex.com/unknown",
        "https://ex.com/fs-news",
        "https://ex.com/healthcare",
    }
    limited = select_field_backfill_targets(processed, field="actor_employer_sector", limit=1)
    assert [r.link for r in limited] == ["https://ex.com/unknown"]


def test_field_backfill_dry_run_prints_counts_only(tmp_path, monkeypatch, capsys) -> None:
    from apps.aggregator.__main__ import main

    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [
            {"link": "https://ex.com/secret-case-one", "channel": "news", "source_id": "example"},
            {"link": "https://ex.com/secret-case-two", "industry": "unknown"},
        ],
    )
    rc = main(
        [
            "backfill_field",
            "--field",
            "actor_employer_sector",
            "--dry-run",
            "--processed-path",
            str(processed),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "2" in out and "news: 1" in out and "filings: 1" in out
    assert "financial-services: 1" in out and "unknown: 1" in out
    assert "secret-case" not in out and "Case " not in out
    # Dry run clears nothing.
    assert all(r.forensics is not None for r in JsonlProcessedStore(processed).load_all())


def test_field_backfill_rejects_unknown_field(tmp_path, capsys) -> None:
    from apps.aggregator.__main__ import main

    rc = main(
        [
            "backfill_field",
            "--field",
            "industry",
            "--dry-run",
            "--processed-path",
            str(tmp_path / "x.jsonl"),
        ]
    )
    assert rc == 2


def test_field_backfill_clear_preserves_history_and_reenriches(tmp_path, monkeypatch) -> None:
    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [{"link": "https://ex.com/case", "channel": "news", "source_id": "example"}],
    )
    before = JsonlProcessedStore(processed).load_all()[0]
    assert before.enrichment_history
    history_before = [h.model_dump(mode="json") for h in before.enrichment_history]

    cleared = clear_field_backfill_targets(processed, field="actor_employer_sector")
    assert cleared == 1
    row = JsonlProcessedStore(processed).load_all()[0]
    assert row.forensics is None and row.ai_summary is None
    assert [h.model_dump(mode="json") for h in row.enrichment_history] == history_before

    # Next sweep: the new generation answers the question; the overlay fills it.
    from tests.test_summarize import _reply

    fresh = FakeEnricher(reply=_reply(actor_employer_sector="professional-services"))
    fresh.model_name = TARGET
    _install(monkeypatch, fresh)
    raw_path = tmp_path / "raw.jsonl"
    run_processing(raw_path=raw_path, processed_path=processed)
    after = JsonlProcessedStore(processed).load_all()[0]
    assert after.forensics is not None
    assert after.forensics.actor_employer_sector == "professional-services"
    assert len(after.enrichment_history) >= len(history_before)
    assert select_field_backfill_targets(processed, field="actor_employer_sector") == []
