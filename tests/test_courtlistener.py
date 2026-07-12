"""Tests for CourtListener RECAP → RawArticle mapping."""

from __future__ import annotations

from apps.aggregator.config import DEFAULT_FEEDS, get_enabled_feeds
from apps.aggregator.courtlistener import hit_to_raw_article, parse_queries


def test_hit_to_raw_article_maps_recap_docket() -> None:
    hit = {
        "caseName": "United States v. Example",
        "docket_absolute_url": "/docket/123/united-states-v-example/",
        "court": "District Court, S.D. New York",
        "docketNumber": "1:24-cr-00001",
        "cause": "18:1832 Trade Secrets",
        "dateFiled": "2024-06-01",
        "party": ["United States", "Jane Example"],
    }
    article = hit_to_raw_article(hit, query='"trade secret"')
    assert article is not None
    assert article.title == "United States v. Example"
    assert article.link.startswith("https://www.courtlistener.com/docket/")
    assert article.source_id == "courtlistener-recap"
    assert article.published is not None
    assert article.summary is not None
    assert "Trade Secrets" in article.summary
    assert "Jane Example" in article.summary
    assert "trade secret" in article.summary.lower()


def test_hit_to_raw_article_skips_incomplete() -> None:
    assert hit_to_raw_article({"caseName": "No link"}, query="x") is None


def test_parse_queries_defaults_and_overrides() -> None:
    defaults = parse_queries("")
    assert len(defaults) >= 3
    assert parse_queries("a, b , ,c") == ["a", "b", "c"]


def test_crypto_feeds_disabled_by_default() -> None:
    crypto = [f for f in DEFAULT_FEEDS if f.category == "insider-crypto"]
    assert crypto
    assert all(not f.enabled for f in crypto)
    enabled_ids = {f.id for f in get_enabled_feeds()}
    assert "coincentral" not in enabled_ids
    assert "doj-press" in enabled_ids
    assert "sec-litigation" in enabled_ids
