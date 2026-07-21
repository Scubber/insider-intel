"""Targeted re-enrich of 'missed' filings (forensics from a non-target model)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.reenrich import clear_missed_filings, select_missed_filings
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from tests.test_summarize import FakeEnricher, _install

TARGET = "claude-sonnet-5"
BODY = "Insider data exfiltration via removable media, trade secret theft. " * 40


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
