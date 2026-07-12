"""Tests for Feedly entry → RawArticle mapping."""

from __future__ import annotations

from apps.aggregator.feedly import entry_to_raw_article


def test_entry_to_raw_article_maps_board_item() -> None:
    entry = {
        "id": "tag:feedly.com,example:entry",
        "title": "Apple Sues OpenAI: Former Employees Accused of Stealing iPhone Secrets",
        "canonicalUrl": "https://coincentral.com/apple-sues-openai/",
        "published": 1_720_000_000_000,
        "summary": {
            "content": (
                "<p>Apple alleges former employees downloaded confidential "
                "engineering documents before leaving.</p>"
            )
        },
        "origin": {"title": "CoinCentral", "streamId": "feed/https://coincentral.com/feed"},
        "categories": [{"label": "Insider Threats x Top Stories"}],
        "keywords": ["ITM-Hunt"],
    }
    article = entry_to_raw_article(
        entry,
        stream_id="user/demo/tag/insider-threats-x-top-stories",
        stream_label="Insider Threats x Top Stories",
    )
    assert article is not None
    assert article.title.startswith("Apple Sues OpenAI")
    assert article.link.startswith("https://coincentral.com/")
    assert "CoinCentral" in article.source_name
    assert article.source_id.startswith("feedly-")
    assert article.summary is not None
    assert "downloaded confidential" in article.summary.lower()
    assert "Insider Threats x Top Stories" in article.summary
    assert "ITM-Hunt" in article.summary
    assert article.published is not None


def test_entry_to_raw_article_skips_incomplete() -> None:
    assert entry_to_raw_article({"title": "No link"}, stream_id="x") is None
