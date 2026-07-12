"""Channel provenance filter (news / filings / tips)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle
from shared.schemas.articles import ExtractedEntities, resolve_channel


def _article(*, source_id: str, channel: str = "news", title: str = "t") -> ProcessedArticle:
    return ProcessedArticle(
        title=title,
        link=f"https://example.com/{source_id}/{title}",
        published=datetime(2024, 1, 1, tzinfo=UTC),
        source_id=source_id,
        source_name=source_id,
        channel=channel,  # type: ignore[arg-type]
        summary="summary",
        clean_text="clean",
        entities=ExtractedEntities(),
        relevance_score=0.5,
        itm_alignment="insider",
    )


def test_resolve_channel_heuristics() -> None:
    assert resolve_channel("reddit-netsec", "news") == "tips"
    assert resolve_channel("courtlistener-recap", "news") == "filings"
    assert resolve_channel("krebsonsecurity", "news") == "news"
    assert resolve_channel("unit42", "tips") == "tips"


def test_channel_filter_on_index() -> None:
    index = ArticleSearchIndex(
        [
            _article(source_id="krebs", channel="news", title="news-a"),
            _article(source_id="courtlistener-recap", channel="filings", title="filing-a"),
            _article(source_id="reddit-netsec", channel="tips", title="tip-a"),
        ]
    )
    news = index.list_articles(limit=10, min_score=0, itm_alignment="all", channel="news")
    filings = index.list_articles(limit=10, min_score=0, itm_alignment="all", channel="filings")
    tips = index.list_articles(limit=10, min_score=0, itm_alignment="all", channel="tips")
    assert news.count == 1 and news.results[0].channel == "news"
    assert filings.count == 1 and filings.results[0].channel == "filings"
    assert tips.count == 1 and tips.results[0].channel == "tips"
