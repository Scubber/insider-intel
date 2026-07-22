"""Tests for CourtListener search hit → RawArticle mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from apps.aggregator.config import DEFAULT_FEEDS, get_enabled_feeds
from apps.aggregator.courtlistener import (
    _first_opinion_id,
    _search,
    company_watchlist_queries,
    fetch_opinion_text,
    hit_to_raw_article,
    opinion_hit_to_raw_article,
    parse_queries,
    parse_types,
)


def test_company_watchlist_expands_to_scoped_and_catchall() -> None:
    queries = company_watchlist_queries("Voya, Voya India")
    # Two companies × (scoped, catch-all), scoped first, order preserved.
    assert queries == [
        '"Voya" (employee OR "former employee" OR contractor OR insider '
        'OR "trade secret" OR misappropriation OR "economic espionage" '
        'OR fraud OR embezzlement OR "data breach" OR confidential OR proprietary)',
        '"Voya"',
        '"Voya India" (employee OR "former employee" OR contractor OR insider '
        'OR "trade secret" OR misappropriation OR "economic espionage" '
        'OR fraud OR embezzlement OR "data breach" OR confidential OR proprietary)',
        '"Voya India"',
    ]


def test_company_watchlist_empty_and_blank_yield_nothing() -> None:
    assert company_watchlist_queries("") == []
    assert company_watchlist_queries("   ") == []
    assert company_watchlist_queries(None) == []
    # Blank segments between commas are skipped.
    assert company_watchlist_queries("Voya, ,") == [
        '"Voya" (employee OR "former employee" OR contractor OR insider '
        'OR "trade secret" OR misappropriation OR "economic espionage" '
        'OR fraud OR embezzlement OR "data breach" OR confidential OR proprietary)',
        '"Voya"',
    ]


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
    assert opinion_hit_to_raw_article({"absolute_url": "/opinion/1/"}, query="x") is None


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
    # query tag lives in content (scored, not displayed); opinion body appends
    assert articles[0].content is not None
    assert articles[0].content.startswith("CourtListener query:")
    assert articles[0].content.endswith("full opinion body")
    # detail 500 never drops the article; content keeps just the query tag
    assert articles[1].content == "CourtListener query: insider"


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


def test_watermark_written_and_used_on_next_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_watermark_not_advanced_on_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_since_overrides_watermark_read(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_no_watermark_disables_read_and_write(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_same_link_across_queries_saved_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


# --- Full-text backfill (RECAP archive / opinion cluster) ---------------------


def test_parse_ids_from_links() -> None:
    from apps.aggregator.courtlistener import parse_docket_id, parse_opinion_id

    assert parse_docket_id("https://www.courtlistener.com/docket/123/us-v-x/") == 123
    assert parse_docket_id("https://example.com/no-docket/") is None
    assert parse_opinion_id("https://www.courtlistener.com/opinion/456/us-v-x/") == 456
    assert parse_opinion_id(None) is None


def test_fetch_recap_document_text_concatenates_available_docs() -> None:
    from apps.aggregator.courtlistener import fetch_recap_document_text

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "results": [
                        {
                            "plain_text": "COMPLAINT: the defendant copied files.",
                            "description": "Complaint",
                            "document_number": 1,
                        },
                        {"plain_text": "", "description": "Sealed", "document_number": 2},
                        {
                            "plain_text": "ORDER granting motion.",
                            "description": "Order",
                            "document_number": 3,
                        },
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text = fetch_recap_document_text(123, client=client)

    params = dict(requests[0].url.params)
    assert params["docket_entry__docket__id"] == "123"
    assert params["is_available"] == "true"
    assert "plain_text" in params["fields"]
    assert text.index("Complaint") < text.index("Order")
    assert "defendant copied files" in text
    assert "Sealed" not in text  # empty plain_text skipped


def test_fetch_recap_document_text_empty_archive_is_not_an_error() -> None:
    from apps.aggregator.courtlistener import fetch_recap_document_text

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"results": []}))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_recap_document_text(1, client=client) == ""


def test_fetch_recap_document_text_respects_cap() -> None:
    from apps.aggregator.courtlistener import fetch_recap_document_text

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps({"results": [{"plain_text": "x" * 5000, "document_number": 1}]}),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text = fetch_recap_document_text(1, max_chars=600, client=client)
    assert len(text) <= 600


def test_fetch_cluster_opinion_text_follows_sub_opinion() -> None:
    from apps.aggregator.courtlistener import fetch_cluster_opinion_text

    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.startswith("/api/rest/v4/clusters/"):
            return httpx.Response(
                200,
                text=json.dumps(
                    {"sub_opinions": ["https://www.courtlistener.com/api/rest/v4/opinions/9/"]}
                ),
            )
        return httpx.Response(200, text=json.dumps({"plain_text": "OPINION BODY"}))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text = fetch_cluster_opinion_text(456, client=client)
    assert text == "OPINION BODY"
    assert paths == ["/api/rest/v4/clusters/456/", "/api/rest/v4/opinions/9/"]


def test_storage_refresh_force_rewrites_content() -> None:
    import tempfile
    from pathlib import Path

    from apps.aggregator.storage import JsonlArticleStore
    from shared.schemas import RawArticle

    with tempfile.TemporaryDirectory() as tmp:
        store = JsonlArticleStore(Path(tmp) / "raw.jsonl")
        original = RawArticle(
            title="US v. X",
            link="https://www.courtlistener.com/docket/1/us-v-x/",
            summary="Court: SDNY",
            content="CourtListener query: q",
            source_id="courtlistener-recap",
            source_name="CourtListener RECAP",
            channel="filings",
        )
        store.save([original])

        enriched = original.model_copy(
            update={"content": "CourtListener query: q\nFULL DOCUMENT TEXT"}
        )
        # Default refresh cannot see a content-only change (same fingerprint,
        # content already non-empty) …
        assert store.refresh([enriched]) == (0, 0)
        # … force rewrites it.
        assert store.refresh([enriched], force=True) == (0, 1)
        rows = JsonlArticleStore(Path(tmp) / "raw.jsonl").load_all()
        assert "FULL DOCUMENT TEXT" in (rows[0].content or "")


def _stored_docket(link: str, *, content: str = "CourtListener query: q"):
    from shared.schemas import RawArticle

    return RawArticle(
        title=f"US v. {link.rsplit('/', 2)[-2]}",
        link=link,
        summary="Court: SDNY\nDocket: 1:24-cr-00001",
        content=content,
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )


def test_text_backfill_enriches_and_resets_llm_fields(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.process_pipeline import run_processing
    from apps.aggregator.processed_storage import JsonlProcessedStore
    from apps.aggregator.storage import JsonlArticleStore
    from shared.schemas import CaseRecord

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save(
        [
            _stored_docket("https://www.courtlistener.com/docket/11/a/"),
            _stored_docket(
                "https://www.courtlistener.com/docket/12/b/",
                content="CourtListener query: q\nALREADY HAS BODY",
            ),
        ]
    )
    run_processing(raw_path=raw_path, processed_path=processed_path)

    # Simulate a prior thin LLM extraction on the query-tag-only docket.
    pstore = JsonlProcessedStore(processed_path)
    rows = {r.link: r for r in pstore.load_all()}
    thin = rows["https://www.courtlistener.com/docket/11/a/"].model_copy(
        update={
            "ai_summary": "thin",
            "case_record": CaseRecord(is_insider_case=True, methods=["old"]),
        }
    )
    pstore.upsert([thin])

    monkeypatch.setattr(
        clp, "fetch_recap_document_text", lambda docket_id, **kw: "FULL FILING TEXT " * 10
    )
    result = clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(processed_path),
        state=clp.JsonIngestState(tmp_path / "state.json"),
    )
    assert result.total_articles_saved == 1  # only the query-tag-only row

    raw_rows = {a.link: a for a in JsonlArticleStore(raw_path).load_all()}
    assert "FULL FILING TEXT" in (
        raw_rows["https://www.courtlistener.com/docket/11/a/"].content or ""
    )
    assert "ALREADY HAS BODY" in (
        raw_rows["https://www.courtlistener.com/docket/12/b/"].content or ""
    )

    cleared = {r.link: r for r in JsonlProcessedStore(processed_path).load_all()}[
        "https://www.courtlistener.com/docket/11/a/"
    ]
    assert cleared.ai_summary is None and cleared.case_record is None

    # The enriched raw row is newer than its processed row → next processing
    # run re-scores it with the full text in clean_text.
    rerun = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert rerun.articles_processed == 1
    reprocessed = {r.link: r for r in JsonlProcessedStore(processed_path).load_all()}[
        "https://www.courtlistener.com/docket/11/a/"
    ]
    assert "FULL FILING TEXT" in reprocessed.clean_text


def test_text_backfill_respects_limit_and_retry_window(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.storage import JsonlArticleStore

    raw_path = tmp_path / "raw.jsonl"
    JsonlArticleStore(raw_path).save(
        [_stored_docket(f"https://www.courtlistener.com/docket/{n}/case{n}/") for n in range(1, 5)]
    )
    calls: list[int] = []

    def fake_fetch(docket_id, **kw):
        calls.append(docket_id)
        return ""  # archive has nothing yet

    monkeypatch.setattr(clp, "fetch_recap_document_text", fake_fetch)
    state = clp.JsonIngestState(tmp_path / "state.json")
    clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
        limit=2,
    )
    assert len(calls) == 2  # per-run attempt cap

    # Attempts are remembered: an immediate re-run moves on to fresh links
    # instead of re-hitting the same dockets inside the retry window.
    clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
        limit=10,
    )
    assert len(calls) == 4
    assert len(set(calls)) == 4


def test_text_backfill_429_aborts_sweep_without_marking_attempts(tmp_path, monkeypatch) -> None:
    """A throttled run must not burn the 7-day retry window for its links."""
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.courtlistener import CourtListenerError
    from apps.aggregator.storage import JsonlArticleStore

    raw_path = tmp_path / "raw.jsonl"
    links = [f"https://www.courtlistener.com/docket/{n}/case{n}/" for n in range(1, 4)]
    JsonlArticleStore(raw_path).save([_stored_docket(link) for link in links])
    calls: list[int] = []

    def throttled(docket_id, **kw):
        calls.append(docket_id)
        raise CourtListenerError(f"docket {docket_id} recap HTTP 429: throttled")

    monkeypatch.setattr(clp, "fetch_recap_document_text", throttled)
    state = clp.JsonIngestState(tmp_path / "state.json")
    clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
        limit=10,
    )
    assert len(calls) == 1  # first 429 stops the sweep — no hammering
    for link in links:
        assert state.get(clp._TEXT_ATTEMPT_KEY.format(link=link)) is None

    # Once the throttle clears, the very next run fetches normally.
    monkeypatch.setattr(clp, "fetch_recap_document_text", lambda docket_id, **kw: "FULL TEXT " * 5)
    result = clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
        limit=10,
    )
    assert result.total_articles_saved == 3


def test_non_throttle_error_does_not_start_retry_clock(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.courtlistener import CourtListenerError
    from apps.aggregator.storage import JsonlArticleStore

    raw_path = tmp_path / "raw.jsonl"
    link = "https://www.courtlistener.com/docket/1/a/"
    JsonlArticleStore(raw_path).save([_stored_docket(link)])

    def flaky(docket_id, **kw):
        raise CourtListenerError("docket 1 recap fetch failed: connection reset")

    monkeypatch.setattr(clp, "fetch_recap_document_text", flaky)
    state = clp.JsonIngestState(tmp_path / "state.json")
    clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
    )
    assert state.get(clp._TEXT_ATTEMPT_KEY.format(link=link)) is None


def test_legacy_attempt_markers_are_retried(tmp_path, monkeypatch) -> None:
    """Markers written before the 'checked @' format (incl. 429-poisoned ones)
    must not lock links out — they are treated as never-attempted once."""
    from datetime import UTC, datetime

    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.storage import JsonlArticleStore

    raw_path = tmp_path / "raw.jsonl"
    link = "https://www.courtlistener.com/docket/1/a/"
    JsonlArticleStore(raw_path).save([_stored_docket(link)])
    state = clp.JsonIngestState(tmp_path / "state.json")
    # Legacy format: bare ISO timestamp from a pre-fix (possibly throttled) run.
    state.set(clp._TEXT_ATTEMPT_KEY.format(link=link), datetime.now(UTC).isoformat())

    monkeypatch.setattr(clp, "fetch_recap_document_text", lambda docket_id, **kw: "FULL TEXT " * 5)
    result = clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=state,
    )
    assert result.total_articles_saved == 1
    assert (state.get(clp._TEXT_ATTEMPT_KEY.format(link=link)) or "").startswith("checked @")


def test_throttle_wait_and_retry_honors_hint(tmp_path, monkeypatch) -> None:
    """A 429 with a refill hint waits it out once and retries, then succeeds."""
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.courtlistener import CourtListenerError
    from apps.aggregator.storage import JsonlArticleStore

    monkeypatch.setenv("COURTLISTENER_REQUEST_DELAY_SECONDS", "0.01")
    raw_path = tmp_path / "raw.jsonl"
    link = "https://www.courtlistener.com/docket/1/a/"
    JsonlArticleStore(raw_path).save([_stored_docket(link)])

    sleeps: list[float] = []
    monkeypatch.setattr(clp.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def flaky_then_ok(docket_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise CourtListenerError(
                'docket 1 recap HTTP 429: {"detail":"Request was throttled. '
                'Rate limit exceeded: 10/min. Expected available in 51 seconds."}'
            )
        return "FULL TEXT " * 5

    monkeypatch.setattr(clp, "fetch_recap_document_text", flaky_then_ok)
    result = clp.run_courtlistener_text_backfill(
        store_path=str(raw_path),
        processed_path=str(tmp_path / "processed.jsonl"),
        state=clp.JsonIngestState(tmp_path / "state.json"),
    )
    assert calls["n"] == 2
    assert result.total_articles_saved == 1
    assert any(50 < s < 60 for s in sleeps)  # honored "51 seconds" + margin


# --- Rolling historical sweep -------------------------------------------------


def _history_search_stub(calls):
    from shared.schemas import RawArticle

    def fake_search(*, search_type, query, filed_after=None, filed_before=None, **kw):
        calls.append((search_type, query, filed_after, filed_before))
        return [
            RawArticle(
                title=f"US v. {search_type} {len(calls)}",
                link=f"https://www.courtlistener.com/docket/{9000 + len(calls)}/x/",
                summary="Court: SDNY",
                content="CourtListener query: h",
                source_id="courtlistener-recap",
                source_name="CourtListener RECAP",
                channel="filings",
            )
        ]

    return fake_search


def test_history_sweep_walks_windows_back_to_floor(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp

    calls: list[tuple] = []
    monkeypatch.setattr(clp, "_search", _history_search_stub(calls))
    monkeypatch.setenv("COURTLISTENER_HISTORY_WINDOW_DAYS", "90")
    # per-window = 0 → all rotation queries in one run, so the cursor walks back
    # each run (the pre-rotation behaviour, now covering the full query set).
    monkeypatch.setenv("COURTLISTENER_HISTORY_QUERIES_PER_WINDOW", "0")
    state = clp.JsonIngestState(tmp_path / "state.json")
    nq = len(clp.history_rotation_queries())

    r1 = clp.run_courtlistener_history_sweep(store_path=str(tmp_path / "raw.jsonl"), state=state)
    assert r1.total_articles_saved == nq * 2  # nq queries x 2 types, unique links
    assert len(calls) == nq * 2
    _, _, since1, before1 = calls[0]
    assert since1 < before1
    cursor_after_1 = state.get("courtlistener_history:cursor")
    assert cursor_after_1 == since1

    # Second run continues from the stored cursor — windows never overlap.
    clp.run_courtlistener_history_sweep(store_path=str(tmp_path / "raw.jsonl"), state=state)
    _, _, since2, before2 = calls[nq * 2]
    assert before2 == since1
    assert since2 < before2


def test_history_sweep_rotates_queries_within_window(tmp_path, monkeypatch) -> None:
    """A small per-window slice holds the cursor until the window is fully covered."""
    import math

    import apps.aggregator.courtlistener_pipeline as clp

    calls: list[tuple] = []
    monkeypatch.setattr(clp, "_search", _history_search_stub(calls))
    monkeypatch.setenv("COURTLISTENER_HISTORY_WINDOW_DAYS", "90")
    monkeypatch.setenv("COURTLISTENER_HISTORY_QUERIES_PER_WINDOW", "4")
    state = clp.JsonIngestState(tmp_path / "state.json")
    rotation = clp.history_rotation_queries()
    runs_per_window = math.ceil(len(rotation) / 4)

    # First run: only a 4-query slice runs; cursor is held, offset advances.
    clp.run_courtlistener_history_sweep(store_path=str(tmp_path / "raw.jsonl"), state=state)
    assert len(calls) == 4 * 2  # 4 queries x 2 types
    assert state.get("courtlistener_history:cursor") is None  # window not finished
    assert state.get("courtlistener_history:qoffset") == "4"
    # Gap-first: the previously-skipped queries lead — no legacy-core query yet.
    assert {c[1] for c in calls}.isdisjoint(clp._HISTORY_LEGACY_CORE)

    # Finish the window's rotation → cursor steps back, offset resets.
    for _ in range(runs_per_window - 1):
        clp.run_courtlistener_history_sweep(store_path=str(tmp_path / "raw.jsonl"), state=state)
    assert state.get("courtlistener_history:cursor") is not None
    assert state.get("courtlistener_history:qoffset") == "0"
    # Every rotation query ran within the single window (both types).
    assert {c[1] for c in calls} == set(rotation)
    assert '"scattered spider"' in {c[1] for c in calls}


def test_history_sweep_stops_at_floor_and_respects_disable(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp

    calls: list[tuple] = []
    monkeypatch.setattr(clp, "_search", _history_search_stub(calls))
    state = clp.JsonIngestState(tmp_path / "state.json")
    state.set("courtlistener_history:cursor", "2015-01-01")  # at the default floor

    result = clp.run_courtlistener_history_sweep(
        store_path=str(tmp_path / "raw.jsonl"), state=state
    )
    assert calls == [] and result.sources == []

    monkeypatch.setenv("COURTLISTENER_HISTORY_FLOOR", "")
    state2 = clp.JsonIngestState(tmp_path / "state2.json")
    result2 = clp.run_courtlistener_history_sweep(
        store_path=str(tmp_path / "raw2.jsonl"), state=state2
    )
    assert calls == [] and result2.sources == []


def test_history_sweep_throttle_does_not_advance_cursor(tmp_path, monkeypatch) -> None:
    import apps.aggregator.courtlistener_pipeline as clp
    from apps.aggregator.courtlistener import CourtListenerError

    def throttled(**kw):
        raise CourtListenerError("HTTP 429: throttled")

    monkeypatch.setattr(clp, "_search", throttled)
    state = clp.JsonIngestState(tmp_path / "state.json")
    clp.run_courtlistener_history_sweep(store_path=str(tmp_path / "raw.jsonl"), state=state)
    assert state.get("courtlistener_history:cursor") is None  # window retries next run
