"""Tests for web keyword alert RSS → RawArticle mapping."""

from __future__ import annotations

from types import SimpleNamespace

from apps.aggregator.web_keywords import entry_to_raw_article


def test_alert_entry_to_raw_article() -> None:
    entry = SimpleNamespace(
        title="Ex-employee charged in trade secret theft",
        link="https://news.example.com/trade-secret-case",
        summary="<p>Former contractor allegedly stole customer lists.</p>",
        published="Mon, 01 Jul 2024 12:00:00 GMT",
    )
    article = entry_to_raw_article(
        entry,
        feed_url="https://www.google.com/alerts/feeds/abc123",
    )
    assert article is not None
    assert article.source_id == "web-keyword"
    assert "trade secret" in article.title.lower()
    assert article.summary is not None
    assert "Alert feed:" in article.summary
    assert article.published is not None


def test_alert_entry_skips_incomplete() -> None:
    assert (
        entry_to_raw_article(
            SimpleNamespace(title="No link", link="", summary=""),
            feed_url="https://example.com/feed",
        )
        is None
    )
