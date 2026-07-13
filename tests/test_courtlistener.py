"""Tests for CourtListener search hit → RawArticle mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from apps.aggregator.config import DEFAULT_FEEDS, get_enabled_feeds
from apps.aggregator.courtlistener import (
    _first_opinion_id,
    _search,
    fetch_opinion_text,
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
    state = clp.JsonIngestState(tmp_path / "state.json")
    result = clp.run_courtlistener_ingestion(
        types=["all"],
        queries=["insider"],
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )
    assert calls == [("dockets", "insider"), ("opinions", "insider")]
    assert [s.source_id for s in result.sources] == [
        "courtlistener-recap",
        "courtlistener-opinions",
    ]
    assert all(s.success for s in result.sources)
    assert result.total_articles_saved == 2
    assert state.get("courtlistener:dockets") is not None
    assert state.get("courtlistener:opinions") is not None


def test_first_opinion_id_ignores_cluster_id() -> None:
    # Top-level "id" is the opinion *cluster* id — must never be used.
    assert _first_opinion_id({"id": 99, "opinions": [{"id": 7}]}) == 7
    assert _first_opinion_id({"id": 99}) is None
    assert _first_opinion_id({"opinions": [{"snippet": "x"}, {"id": 3}]}) == 3
    assert _first_opinion_id({"opinions": "bogus"}) is None


def test_fetch_opinion_text_prefers_plain_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/rest/v4/opinions/7/"
        assert "plain_text" in request.url.params["fields"]
        return httpx.Response(200, json={"plain_text": "  the full body  "})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_opinion_text(7, client=client) == "the full body"


def test_fetch_opinion_text_html_fallback_and_truncation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"plain_text": "", "html": "<p>employee copied files</p>"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_opinion_text(7, client=client) == "employee copied files"
        assert fetch_opinion_text(7, client=client, max_chars=8) == "employee"


def _routing_transport(
    hits: list[dict], detail_responses: dict[str, httpx.Response]
) -> tuple[httpx.MockTransport, list[str]]:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.startswith("/api/rest/v4/opinions/"):
            return detail_responses[request.url.path]
        return httpx.Response(200, json={"results": hits, "next": None})

    return httpx.MockTransport(handler), paths


def test_search_opinions_fetch_content_survives_detail_error() -> None:
    hits = [
        {
            "caseName": "United States v. A",
            "absolute_url": "/opinion/1/a/",
            "opinions": [{"id": 7}],
        },
        {
            "caseName": "United States v. B",
            "absolute_url": "/opinion/2/b/",
            "opinions": [{"id": 8}],
        },
    ]
    transport, _ = _routing_transport(
        hits,
        {
            "/api/rest/v4/opinions/7/": httpx.Response(
                200, json={"plain_text": "full opinion body"}
            ),
            "/api/rest/v4/opinions/8/": httpx.Response(500, text="boom"),
        },
    )
    with httpx.Client(transport=transport) as client:
        articles = _search(
            search_type="opinions",
            query="insider",
            fetch_content=True,
            client=client,
        )
    assert len(articles) == 2
    assert articles[0].content == "full opinion body"
    assert articles[1].content is None  # detail 500 never drops the article


def test_search_dockets_never_fetches_detail() -> None:
    hits = [
        {
            "caseName": "United States v. C",
            "docket_absolute_url": "/docket/3/c/",
        }
    ]
    transport, paths = _routing_transport(hits, {})
    with httpx.Client(transport=transport) as client:
        articles = _search(
            search_type="dockets",
            query="insider",
            fetch_content=True,  # no enricher on the dockets spec
            client=client,
        )
    assert len(articles) == 1
    assert paths == ["/api/rest/v4/search/"]


def test_search_passes_filed_after() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [], "next": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _search(
            search_type="dockets",
            query="insider",
            filed_after="2026-07-01",
            client=client,
        )
    assert dict(requests[0].url.params)["filed_after"] == "2026-07-01"


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


def _fake_search_recorder(results=None):
    calls: list[dict] = []

    def fake_search(*, search_type, query, filed_after=None, **kwargs):
        calls.append({"type": search_type, "query": query, "filed_after": filed_after})
        return list(results or [])

    return fake_search, calls


def test_watermark_written_and_used_on_next_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from datetime import UTC, date, datetime, timedelta

    from apps.aggregator import courtlistener_pipeline as clp
    from shared.settings import get_settings

    fake_search, calls = _fake_search_recorder()
    monkeypatch.setattr(clp, "_search", fake_search)
    state = clp.JsonIngestState(tmp_path / "state.json")
    kwargs = dict(
        types=["dockets"],
        queries=["q"],
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )

    clp.run_courtlistener_ingestion(**kwargs)
    assert calls[0]["filed_after"] is None  # first run: no watermark
    today = datetime.now(UTC).date().isoformat()
    assert state.get("courtlistener:dockets") == today

    clp.run_courtlistener_ingestion(**kwargs)
    lookback = get_settings().courtlistener_lookback_days
    expected = (date.fromisoformat(today) - timedelta(days=lookback)).isoformat()
    assert calls[1]["filed_after"] == expected


def test_watermark_not_advanced_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from apps.aggregator import courtlistener_pipeline as clp
    from apps.aggregator.courtlistener import CourtListenerError

    def failing_search(**kwargs):
        raise CourtListenerError("boom")

    monkeypatch.setattr(clp, "_search", failing_search)
    state = clp.JsonIngestState(tmp_path / "state.json")
    result = clp.run_courtlistener_ingestion(
        types=["dockets"],
        queries=["q"],
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )
    assert state.get("courtlistener:dockets") is None
    assert not result.sources[0].success


def test_since_overrides_watermark_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from apps.aggregator import courtlistener_pipeline as clp

    fake_search, calls = _fake_search_recorder()
    monkeypatch.setattr(clp, "_search", fake_search)
    state = clp.JsonIngestState(tmp_path / "state.json")
    state.set("courtlistener:dockets", "2026-06-01")
    clp.run_courtlistener_ingestion(
        types=["dockets"],
        queries=["q"],
        since="2020-01-01",
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )
    assert calls[0]["filed_after"] == "2020-01-01"


def test_no_watermark_disables_read_and_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from apps.aggregator import courtlistener_pipeline as clp

    fake_search, calls = _fake_search_recorder()
    monkeypatch.setattr(clp, "_search", fake_search)
    state = clp.JsonIngestState(tmp_path / "state.json")
    state.set("courtlistener:dockets", "2026-06-01")
    clp.run_courtlistener_ingestion(
        types=["dockets"],
        queries=["q"],
        use_watermark=False,
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )
    assert calls[0]["filed_after"] is None
    assert state.get("courtlistener:dockets") == "2026-06-01"  # unchanged


def test_same_link_across_queries_saved_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from apps.aggregator import courtlistener_pipeline as clp
    from shared.schemas import RawArticle

    def fake_search(*, search_type, query, **kwargs):
        return [
            RawArticle(
                title="United States v. Example",
                link="https://www.courtlistener.com/docket/1/example/",
                summary=f"Docket: 1:24-cr-1\nCourtListener query: {query}",
                source_id="courtlistener-recap",
                source_name="CourtListener RECAP",
                channel="filings",
            )
        ]

    monkeypatch.setattr(clp, "_search", fake_search)
    state = clp.JsonIngestState(tmp_path / "state.json")
    kwargs = dict(
        types=["dockets"],
        queries=["q1", "q2"],
        store_path=str(tmp_path / "raw.jsonl"),
        state=state,
    )
    result = clp.run_courtlistener_ingestion(**kwargs)
    assert result.total_articles_saved == 1

    # Re-run: same content (first query wins) → no rewrite, nothing saved.
    result2 = clp.run_courtlistener_ingestion(**kwargs)
    assert result2.total_articles_saved == 0
