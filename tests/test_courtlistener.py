"""Tests for CourtListener search hit → RawArticle mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from apps.aggregator.config import DEFAULT_FEEDS, get_enabled_feeds
from apps.aggregator.courtlistener import (
    _search,
    hit_to_raw_article,
    opinion_hit_to_raw_article,
    parse_queries,
    parse_types,
)


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


def test_opinion_hit_to_raw_article_maps_fields() -> None:
    hit = {
        "caseName": "United States v. Example",
        "absolute_url": "/opinion/456/united-states-v-example/",
        "court": "Court of Appeals, Second Circuit",
        "docketNumber": "23-1234",
        "dateFiled": "2024-05-15",
        "citation": ["100 F.4th 100", "2024 WL 123456"],
        "opinions": [{"snippet": "the employee copied trade secret files"}],
    }
    article = opinion_hit_to_raw_article(hit, query='"trade secret"')
    assert article is not None
    assert article.title == "United States v. Example"
    assert article.link.startswith("https://www.courtlistener.com/opinion/")
    assert article.source_id == "courtlistener-opinions"
    assert article.channel == "filings"
    assert article.published is not None
    assert article.summary is not None
    assert "100 F.4th 100" in article.summary
    assert "copied trade secret files" in article.summary
    assert "trade secret" in article.summary.lower()


def test_opinion_hit_citation_as_string() -> None:
    hit = {
        "caseName": "Doe v. Corp",
        "absolute_url": "/opinion/789/doe-v-corp/",
        "citation": "598 U.S. 175",
        "snippet": "insider misuse of access",
    }
    article = opinion_hit_to_raw_article(hit, query="insider")
    assert article is not None
    assert article.published is None
    assert article.summary is not None
    assert "598 U.S. 175" in article.summary
    assert "insider misuse of access" in article.summary


def test_opinion_hit_to_raw_article_skips_incomplete() -> None:
    assert opinion_hit_to_raw_article({"caseName": "No link"}, query="x") is None
    assert (
        opinion_hit_to_raw_article({"absolute_url": "/opinion/1/"}, query="x") is None
    )


def test_parse_types() -> None:
    assert parse_types("") == ["dockets"]
    assert parse_types(None) == ["dockets"]
    assert parse_types("all") == ["dockets", "opinions"]
    assert parse_types("r, o") == ["dockets", "opinions"]
    assert parse_types("opinions, opinions, dockets") == ["opinions", "dockets"]
    with pytest.raises(ValueError, match="unknown CourtListener search type"):
        parse_types("audio")


def test_search_opinions_params_and_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = {
            "results": [
                {
                    "caseName": "United States v. Example",
                    "absolute_url": "/opinion/456/united-states-v-example/",
                    "dateFiled": "2024-05-15",
                }
            ],
            "next": None,
        }
        return httpx.Response(200, text=json.dumps(payload))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        articles = _search(
            search_type="opinions",
            query="insider",
            max_pages=3,
            client=client,
        )

    assert len(requests) == 1  # stops at next=null despite max_pages=3
    params = dict(requests[0].url.params)
    assert params["type"] == "o"
    assert params["order_by"] == "dateFiled desc"
    assert len(articles) == 1
    assert articles[0].source_id == "courtlistener-opinions"


def test_run_courtlistener_ingestion_emits_result_per_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from apps.aggregator import courtlistener_pipeline as clp
    from shared.schemas import RawArticle

    calls: list[tuple[str, str]] = []

    def fake_search(*, search_type: str, query: str, **kwargs) -> list[RawArticle]:
        calls.append((search_type, query))
        spec = clp.SEARCH_TYPES[search_type]
        return [
            RawArticle(
                title=f"{search_type}: {query}",
                link=f"https://www.courtlistener.com/{search_type}/{len(calls)}/",
                source_id=spec.source_id,
                source_name=spec.source_name,
                channel="filings",
            )
        ]

    monkeypatch.setattr(clp, "_search", fake_search)
    result = clp.run_courtlistener_ingestion(
        types=["all"],
        queries=["insider"],
        store_path=str(tmp_path / "raw.jsonl"),
    )
    assert calls == [("dockets", "insider"), ("opinions", "insider")]
    assert [s.source_id for s in result.sources] == [
        "courtlistener-recap",
        "courtlistener-opinions",
    ]
    assert all(s.success for s in result.sources)
    assert result.total_articles_saved == 2


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
