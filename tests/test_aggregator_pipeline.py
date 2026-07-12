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
