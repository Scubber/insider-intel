"""Tests for budget-capped PACER purchasing via RECAP Fetch."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from apps.aggregator.courtlistener_pipeline import _TEXT_ATTEMPT_KEY
from apps.aggregator.ingest_state import JsonIngestState
from apps.aggregator.pacer_purchase import run_pacer_purchases
from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle


def _docket(link: str) -> RawArticle:
    return RawArticle(
        title="Insider threat: US v. Example trade secret exfiltration",
        link=link,
        summary="Court: SDNY\nDocket: 1:24-cr-00001\ninsider threat data exfiltration",
        content="CourtListener query: q",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )


def _seed(tmp_path, links):
    """Raw + processed stores with qualifying dockets, archive already tried."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_docket(link) for link in links])
    run_processing(raw_path=raw_path, processed_path=processed_path)
    state = JsonIngestState(tmp_path / "state.json")
    now = datetime.now(UTC).isoformat()
    for link in links:
        state.set(_TEXT_ATTEMPT_KEY.format(link=link), now)
    return raw_path, processed_path, state


def _transport(posts, *, entries_payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rest/v4/recap-fetch/":
            posts.append(request)
            return httpx.Response(200, text=json.dumps({"id": 42}))
        if request.url.path == "/api/rest/v4/docket-entries/":
            return httpx.Response(200, text=json.dumps(entries_payload))
        return httpx.Response(404, text="{}")

    return httpx.MockTransport(handler)


ENTRIES_WITH_DOC = {
    "results": [
        {
            "entry_number": 1,
            "recap_documents": [{"id": 777, "is_available": False, "document_number": "1"}],
        }
    ]
}


def _enable(monkeypatch):
    monkeypatch.setenv("PACER_USERNAME", "user")
    monkeypatch.setenv("PACER_PASSWORD", "pass")  # pragma: allowlist secret
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "cl-token")


def test_no_credentials_is_a_silent_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PACER_USERNAME", raising=False)
    monkeypatch.delenv("PACER_PASSWORD", raising=False)
    link = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [link])
    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        result, plan = run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert posts == [] and plan.purchases == [] and result.sources == []


def test_buys_lead_document_and_records_state(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    link = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [link])
    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        result, plan = run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert len(posts) == 1
    body = posts[0].read().decode()
    assert "request_type=2" in body and "recap_document=777" in body
    assert "pacer_username=user" in body
    assert result.total_articles_saved == 1
    assert (state.get(f"pacer_purchase:{link}") or "").startswith("doc @")
    # Attempt key reset → next backfill retries immediately.
    assert (state.get(_TEXT_ATTEMPT_KEY.format(link=link)) or "").startswith("1970")
    quarter = f"{datetime.now(UTC).year}-Q{(datetime.now(UTC).month - 1) // 3 + 1}"
    assert state.get(f"pacer_spend:{quarter}") == "300"
    assert plan.purchases[0].stage == "doc"


def test_no_entries_buys_docket_report_once(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    link = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [link])
    posts: list[httpx.Request] = []
    transport = _transport(posts, entries_payload={"results": []})
    with httpx.Client(transport=transport) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert len(posts) == 1
    body = posts[0].read().decode()
    assert "request_type=1" in body and "docket=11" in body
    assert (state.get(f"pacer_purchase:{link}") or "").startswith("docket @")

    # Same stage again → idempotent, no double-billing while entries pend.
    state.set(_TEXT_ATTEMPT_KEY.format(link=link), datetime.now(UTC).isoformat())
    with httpx.Client(transport=transport) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert len(posts) == 1

    # Entries arrive later → the doc stage buys the PDF.
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert len(posts) == 2
    assert "request_type=2" in posts[1].read().decode()


def test_budget_and_run_caps(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    links = [f"https://www.courtlistener.com/docket/{n}/c{n}/" for n in range(1, 4)]
    raw_path, processed_path, state = _seed(tmp_path, links)
    quarter = f"{datetime.now(UTC).year}-Q{(datetime.now(UTC).month - 1) // 3 + 1}"
    state.set(f"pacer_spend:{quarter}", "2500")  # 200 cents of headroom < 300
    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert posts == []  # budget already effectively exhausted

    state.set(f"pacer_spend:{quarter}", "0")
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            limit=2,
            client=client,
        )
    assert len(posts) == 2  # per-run cap


def test_non_qualifying_and_unchecked_cases_never_bought(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    qualifying = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [qualifying])

    # A non-insider docket (no ITM hits / use case) …
    boring = RawArticle(
        title="In re Routine Procedural Matter",
        link="https://www.courtlistener.com/docket/99/boring/",
        summary="Court: SDNY",
        content="CourtListener query: q",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )
    JsonlArticleStore(raw_path).save([boring])
    run_processing(raw_path=raw_path, processed_path=processed_path)
    state.set(_TEXT_ATTEMPT_KEY.format(link=boring.link), datetime.now(UTC).isoformat())

    # … and a qualifying one the free archive hasn't been checked for yet.
    unchecked = _docket("https://www.courtlistener.com/docket/12/b/")
    JsonlArticleStore(raw_path).save([unchecked])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        _, plan = run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert {p.link for p in plan.purchases} == {qualifying}


def test_already_available_doc_skips_purchase_and_resets_attempt(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    link = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [link])
    available = {
        "results": [
            {
                "entry_number": 1,
                "recap_documents": [{"id": 777, "is_available": True, "document_number": "1"}],
            }
        ]
    }
    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=available)) as client:
        run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert posts == []
    assert (state.get(_TEXT_ATTEMPT_KEY.format(link=link)) or "").startswith("1970")


def test_dry_run_spends_nothing(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch)
    link = "https://www.courtlistener.com/docket/11/a/"
    raw_path, processed_path, state = _seed(tmp_path, [link])
    posts: list[httpx.Request] = []
    with httpx.Client(transport=_transport(posts, entries_payload=ENTRIES_WITH_DOC)) as client:
        result, plan = run_pacer_purchases(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            dry_run=True,
            client=client,
        )
    assert posts == []
    assert len(plan.purchases) == 1 and plan.purchases[0].stage == "doc"
    assert result.total_articles_saved == 0
    assert state.get(f"pacer_purchase:{link}") is None
