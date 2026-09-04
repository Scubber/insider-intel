"""Targeted re-enrich of 'missed' filings (forensics from a non-target model)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.reenrich import (
    clear_missed_filings,
    queue_field_backfill_targets,
    select_field_backfill_targets,
    select_missed_filings,
)
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from tests.test_summarize import FakeEnricher, _install, _reply

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
    # Since 2026-09-04 the sweep projects via project_from_history (select-best
    # over the full history), so the floored generation never becomes the
    # projection in the first place — reconcile finds nothing to restore.
    assert result.reenrich_restored == 0
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


def _rich_reply(**overrides):
    methods = [
        {"action": f"m{i}", "tools": [], "claim_status": "alleged", "evidence_quote": ""}
        for i in range(4)
    ]
    return _reply(confidence=0.9, methods=methods, **overrides)


def _seed_rows(tmp_path: Path, monkeypatch, rows: list[dict]) -> Path:
    """Seed N enriched rows (verdict-true, v3, 4 methods) then patch each per ``rows``.

    Each dict: link, channel, source_id, published, plus optional overrides
    applied to the stored row (industry, sector, verdict, schema, alignment).
    """
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    monkeypatch.setenv("FIELD_BACKFILL_TARGETS_PATH", str(tmp_path / "state" / "queue.json"))
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
    fake = FakeEnricher(reply=_rich_reply())
    fake.model_name = TARGET
    _install(monkeypatch, fake)
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "20")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    store = JsonlProcessedStore(processed_path)
    stored = store.load_all()
    by_link = {r["link"]: r for r in rows}
    patched = []
    for row in stored:
        assert row.forensics is not None, row.link
        spec = by_link[row.link]
        row.forensics.industry = spec.get("industry", "financial-services")
        row.forensics.actor_employer_sector = spec.get("sector")
        row.forensics.is_insider_case = spec.get("verdict", True)
        row.forensics.schema_version = spec.get("schema", 3)
        patched.append(row.model_copy(update={"itm_alignment": spec.get("alignment", "insider")}))
    store.replace_all(patched)
    return processed_path


def _queue_path() -> Path:
    from shared.settings import get_settings

    return Path(get_settings().field_backfill_targets_path)


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
            {
                "link": "https://ex.com/weak",
                "channel": "news",
                "source_id": "example",
                "alignment": "weak",
            },
        ],
    )
    sel = select_field_backfill_targets(processed, field="actor_employer_sector")
    # Newest first: /unknown published 07-02, /fs-news 07-01.
    assert [r.link for r in sel.queued] == ["https://ex.com/unknown", "https://ex.com/fs-news"]
    # A row the sweep's spend gate would refuse is reported, never queued.
    assert [r.link for r in sel.skipped_by_gate] == ["https://ex.com/weak"]
    # Industry set is a parameter; None = any industry (healthcare joins).
    any_ind = select_field_backfill_targets(
        processed, field="actor_employer_sector", industries=None
    )
    assert {r.link for r in any_ind.queued} == {
        "https://ex.com/unknown",
        "https://ex.com/fs-news",
        "https://ex.com/healthcare",
    }
    limited = select_field_backfill_targets(processed, field="actor_employer_sector", limit=1)
    assert [r.link for r in limited.queued] == ["https://ex.com/unknown"]


def test_field_backfill_dry_run_prints_counts_only(tmp_path, monkeypatch, capsys) -> None:
    from apps.aggregator.__main__ import main

    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [
            {"link": "https://ex.com/secret-case-one", "channel": "news", "source_id": "example"},
            {"link": "https://ex.com/secret-case-two", "industry": "unknown"},
            {
                "link": "https://ex.com/secret-case-weak",
                "channel": "news",
                "source_id": "example",
                "alignment": "weak",
            },
        ],
    )
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "60")
    rc = main(["backfill_field", "--dry-run", "--processed-path", str(processed)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued: 2" in out and "skipped_by_gate: 1" in out
    assert "news: 1" in out and "filings: 1" in out
    assert "financial-services: 1" in out and "unknown: 1" in out
    assert "secret-case" not in out and "Case " not in out
    # Dry run writes nothing and clears nothing.
    assert not _queue_path().exists()
    assert all(r.forensics is not None for r in JsonlProcessedStore(processed).load_all())


def test_field_backfill_rejects_unknown_field(tmp_path, monkeypatch) -> None:
    from apps.aggregator.__main__ import main

    monkeypatch.setenv("FIELD_BACKFILL_TARGETS_PATH", str(tmp_path / "q.json"))
    rc = main(["backfill_field", "--field", "industry", "--dry-run", "--processed-path", "x"])
    assert rc == 2


def test_field_backfill_default_limit_is_the_reserve(tmp_path, monkeypatch) -> None:
    from apps.aggregator.__main__ import main

    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [
            {"link": f"https://ex.com/c{i}", "channel": "news", "source_id": "example"}
            for i in range(3)
        ],
    )
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "2")
    assert main(["backfill_field", "--processed-path", str(processed)]) == 0
    queue = json.loads(_queue_path().read_text())
    assert queue["field"] == "actor_employer_sector"
    assert len(queue["links"]) == 2
    # Rows are NOT cleared — the projection stays live throughout.
    assert all(r.forensics is not None for r in JsonlProcessedStore(processed).load_all())


def test_thin_reenrich_cannot_gut_projection_but_field_surfaces(tmp_path, monkeypatch) -> None:
    """D1: the sweep projects via select-best; the thin gen only donates the field."""
    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [{"link": "https://ex.com/case", "channel": "news", "source_id": "example"}],
    )
    before = JsonlProcessedStore(processed).load_all()[0]
    assert before.forensics.is_insider_case and len(before.forensics.methods) == 4
    history_before = [h.model_dump(mode="json") for h in before.enrichment_history]
    queue_field_backfill_targets(processed, field="actor_employer_sector", queue_path=_queue_path())
    assert json.loads(_queue_path().read_text())["links"] == ["https://ex.com/case"]

    thin = FakeEnricher(
        reply=_reply(
            is_insider_case=False,
            confidence=0.3,
            methods=[],
            ai_summary="",
            actor_employer_sector="technology",
            outcome=None,
        )
    )
    thin.model_name = "thin-model"
    _install(monkeypatch, thin)
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "5")
    run_processing(raw_path=tmp_path / "raw.jsonl", processed_path=processed)
    assert thin.calls == 1  # billed once, without clearing
    after = JsonlProcessedStore(processed).load_all()[0]
    # Rich record survives: verdict, methods, note all from the winning generation.
    assert after.forensics.is_insider_case is True
    assert len(after.forensics.methods) == 4
    assert after.ai_summary == before.ai_summary
    assert after.case_record.is_insider_case is True
    # ...and the additive field surfaces from the thin generation, stamped.
    assert after.forensics.actor_employer_sector == "technology"
    assert after.forensics.actor_employer_sector_source["model"] == "thin-model"
    # History is append-only and never carries a _source stamp.
    assert [h.model_dump(mode="json") for h in after.enrichment_history][: len(history_before)] == (
        history_before
    )
    assert len(after.enrichment_history) == len(history_before) + 1
    assert all(h.forensics.actor_employer_sector_source is None for h in after.enrichment_history)
    # Queue drained once the generation landed; no longer a target.
    assert json.loads(_queue_path().read_text())["links"] == []
    assert select_field_backfill_targets(processed, field="actor_employer_sector").queued == []

    # A later --force pass through the graph projects identically (idempotent).
    run_processing(raw_path=tmp_path / "raw.jsonl", processed_path=processed, force=True)
    forced = JsonlProcessedStore(processed).load_all()[0]
    assert forced.forensics.model_dump(mode="json") == after.forensics.model_dump(mode="json")
    assert thin.calls == 1
    assert all(h.forensics.actor_employer_sector_source is None for h in forced.enrichment_history)


def test_queued_row_failing_gate_keeps_projection_and_leaves_queue(tmp_path, monkeypatch) -> None:
    """D3: a row the sweep refuses is never stranded — it keeps its record."""
    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [{"link": "https://ex.com/case", "channel": "news", "source_id": "example"}],
    )
    # Queue it by hand (the CLI's gate would have skipped it), then weaken it.
    from apps.aggregator.reenrich import write_field_backfill_queue

    write_field_backfill_queue(
        _queue_path(), field="actor_employer_sector", links=["https://ex.com/case"]
    )
    store = JsonlProcessedStore(processed)
    store.replace_all([store.load_all()[0].model_copy(update={"itm_alignment": "weak"})])
    fake = FakeEnricher(reply=_reply(actor_employer_sector="retail"))
    fake.model_name = TARGET
    _install(monkeypatch, fake)
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "5")
    run_processing(raw_path=tmp_path / "raw.jsonl", processed_path=processed)
    after = store.load_all()[0]
    assert fake.calls == 0  # spend policy still holds
    assert after.forensics is not None and after.forensics.is_insider_case
    assert json.loads(_queue_path().read_text())["links"] == []


def test_queue_survives_until_generation_lands(tmp_path, monkeypatch) -> None:
    """A dead provider leaves the link queued for the next cycle."""
    from tests.test_summarize import ExplodingEnricher

    processed = _seed_rows(
        tmp_path,
        monkeypatch,
        [{"link": "https://ex.com/case", "channel": "news", "source_id": "example"}],
    )
    queue_field_backfill_targets(processed, field="actor_employer_sector", queue_path=_queue_path())
    dead = ExplodingEnricher()
    _install(monkeypatch, dead)
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "5")
    run_processing(raw_path=tmp_path / "raw.jsonl", processed_path=processed)
    assert dead.calls >= 1
    assert json.loads(_queue_path().read_text())["links"] == ["https://ex.com/case"]
    assert JsonlProcessedStore(processed).load_all()[0].forensics is not None
