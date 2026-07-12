"""Tests for keyword / semantic / hybrid search."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.search.index import ArticleSearchIndex
from shared.agents import process_article
from shared.schemas import RawArticle, SearchMode


def _processed(title: str, summary: str, link: str) -> object:
    return process_article(
        RawArticle(
            title=title,
            link=link,
            summary=summary,
            source_id="test",
            source_name="Test",
        )
    )


def test_keyword_search_finds_cve() -> None:
    articles = [
        _processed(
            "Critical bug CVE-2024-55555",
            "Insider threat operators exploit CVE-2024-55555 during data exfiltration.",
            "https://example.com/cve",
        ),
        _processed(
            "Market update",
            "Stocks rose on Friday.",
            "https://example.com/market",
        ),
    ]
    index = ArticleSearchIndex(articles)  # type: ignore[arg-type]
    result = index.search("CVE-2024-55555", mode=SearchMode.keyword, limit=5)
    assert result.count >= 1
    assert result.results[0].link == "https://example.com/cve"


def test_hybrid_search_ranks_threat_content() -> None:
    articles = [
        _processed(
            "Insider risk: privilege abuse and mass download",
            "Departing employee used phishing and lateral movement before exfiltration.",
            "https://example.com/insider",
        ),
        _processed(
            "Baking tips",
            "How to make sourdough bread at home.",
            "https://example.com/bread",
        ),
    ]
    index = ArticleSearchIndex(articles)  # type: ignore[arg-type]
    result = index.search("insider exfiltration privilege", mode=SearchMode.hybrid, limit=5)
    assert result.count >= 1
    assert result.results[0].link == "https://example.com/insider"


def test_search_filters_source_id() -> None:
    a = process_article(
        RawArticle(
            title="Data exfiltration wave",
            link="https://example.com/a",
            summary="Insider threat: data exfiltration by departing employee",
            source_id="alpha",
            source_name="Alpha",
        )
    )
    b = process_article(
        RawArticle(
            title="Data exfiltration wave",
            link="https://example.com/b",
            summary="Insider threat: data exfiltration by departing employee",
            source_id="beta",
            source_name="Beta",
        )
    )
    index = ArticleSearchIndex([a, b])
    result = index.search("exfiltration", source_id="beta")
    assert result.count == 1
    assert result.results[0].source_id == "beta"


def test_list_articles_chronological() -> None:
    older = process_article(
        RawArticle(
            title="Older insider case",
            link="https://example.com/old",
            summary=(
                "Insider threat: departing employee used removable media "
                "for exfiltration after resignation."
            ),
            source_id="test",
            source_name="Test",
            published=datetime(2023, 1, 1, tzinfo=UTC),
        )
    )
    newer = process_article(
        RawArticle(
            title="Newer insider case",
            link="https://example.com/new",
            summary=(
                "Insider threat: departing employee used removable media "
                "for exfiltration after resignation."
            ),
            source_id="test",
            source_name="Test",
            published=datetime(2024, 6, 1, tzinfo=UTC),
        )
    )
    index = ArticleSearchIndex([older, newer])
    listed = index.list_articles(limit=10)
    assert listed.count == 2
    assert listed.results[0].link == "https://example.com/new"
    assert listed.results[0].itm_alignment == "insider"
