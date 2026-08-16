"""Pipeline tests with mocked HTTP."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from apps.aggregator.pipeline import run_ingestion
from shared.schemas import FeedSource

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Mock Feed</title>
    <item>
      <title>Mock Alert</title>
      <link>https://example.com/mock-alert</link>
      <description>Mock summary</description>
    </item>
  </channel>
</rss>
"""


def test_run_ingestion_saves_articles(tmp_path: Path) -> None:
    store_path = tmp_path / "out.jsonl"
    sources = [
        FeedSource(
            id="mock",
            name="Mock",
            url="https://example.com/feed.xml",
        )
    ]

    with patch("apps.aggregator.pipeline.fetch_feed", return_value=SAMPLE_RSS):
        result = run_ingestion(sources=sources, store_path=str(store_path))

    assert result.success_count == 1
    assert result.total_articles_saved == 1
    assert store_path.exists()
    assert "Mock Alert" in store_path.read_text(encoding="utf-8")


def test_run_ingestion_isolates_source_failures(tmp_path: Path) -> None:
    store_path = tmp_path / "out.jsonl"
    sources = [
        FeedSource(id="bad", name="Bad", url="https://example.com/bad.xml"),
        FeedSource(id="good", name="Good", url="https://example.com/good.xml"),
    ]

    def fake_fetch(url: str, **_kwargs: object) -> str:
        if "bad" in url:
            from apps.aggregator.fetcher import FeedFetchError

            raise FeedFetchError(url, "down")
        return SAMPLE_RSS

    with patch("apps.aggregator.pipeline.fetch_feed", side_effect=fake_fetch):
        result = run_ingestion(sources=sources, store_path=str(store_path))

    assert result.failure_count == 1
    assert result.success_count == 1
    assert result.total_articles_saved == 1


def test_enriched_below_min_score_is_kept_and_not_relooped(tmp_path: Path, monkeypatch) -> None:
    """2026-08-16 re-enrichment loop: an article the enricher billed must persist
    even below min_score — dropping it forfeited the spend and, with no
    processed row recorded, the same article re-qualified every cycle."""
    from datetime import UTC, datetime

    from apps.aggregator import process_pipeline
    from apps.aggregator.processed_storage import JsonlProcessedStore
    from apps.aggregator.storage import JsonlArticleStore
    from shared.schemas import ExtractedEntities, ProcessedArticle, RawArticle
    from shared.schemas.forensics import PerCaseForensics

    def _raw(title: str, link: str) -> RawArticle:
        return RawArticle(
            title=title, link=link, summary="s", source_id="example", source_name="Example"
        )

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            _raw("Enriched low scorer", "https://example.com/enriched-low"),
            _raw("Plain low scorer", "https://example.com/plain-low"),
        ]
    )

    def fake_process_article(raw, *, prior=None, budget=None, discover_budget=None):
        enriched = raw.link.endswith("enriched-low")
        return ProcessedArticle(
            title=raw.title,
            link=raw.link,
            published=datetime(2026, 1, 1, tzinfo=UTC),
            source_id=raw.source_id,
            source_name=raw.source_name,
            channel="filings",  # type: ignore[arg-type]
            summary="s",
            clean_text="clean",
            entities=ExtractedEntities(),
            relevance_score=0.05,  # below the gate either way
            itm_alignment="insider",
            ai_summary="note" if enriched else None,
            forensics=(
                PerCaseForensics(link=raw.link, title=raw.title, is_insider_case=True)
                if enriched
                else None
            ),
        )

    monkeypatch.setattr(process_pipeline, "process_article", fake_process_article)

    first = process_pipeline.run_processing(
        raw_path=raw_path, processed_path=processed_path, min_score=0.5
    )
    stored = {a.link: a for a in JsonlProcessedStore(processed_path).load_all()}
    assert "https://example.com/enriched-low" in stored  # paid work persisted
    assert stored["https://example.com/enriched-low"].forensics is not None
    assert "https://example.com/plain-low" not in stored  # unenriched still dropped
    assert first.articles_skipped >= 1

    # Second cycle: the persisted enriched row now satisfies the prior check —
    # no reprocessing loop.
    calls: list = []

    def counting_process_article(raw, **kwargs):
        calls.append(raw.link)
        return fake_process_article(raw, **kwargs)

    monkeypatch.setattr(process_pipeline, "process_article", counting_process_article)
    process_pipeline.run_processing(raw_path=raw_path, processed_path=processed_path, min_score=0.5)
    assert "https://example.com/enriched-low" not in calls
