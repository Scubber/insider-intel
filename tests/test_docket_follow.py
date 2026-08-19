"""Docket follow: poll open verdict-true dockets until an outcome lands."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from apps.aggregator.docket_follow import (
    _ATTEMPT_KEY,
    _CHECKED_PREFIX,
    _DONE_KEY,
    _FILING_KEY,
    run_docket_follow,
    select_follow_candidates,
)
from apps.aggregator.ingest_state import JsonIngestState
from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from shared.schemas.forensics import (
    EnrichmentRecord,
    PerCaseForensics,
    select_best_enrichment,
)
from tests.test_summarize import FakeEnricher, _install, _reply

BODY = "Former employee insider data exfiltration via removable media, trade secret theft. " * 40

DOCKET_LINK = "https://www.courtlistener.com/docket/111/acme-v-smith/"


def _recap(link: str, title: str = "Acme v. Smith") -> RawArticle:
    return RawArticle(
        title=title,
        link=link,
        summary="Court: SDIA\nDocket: 4:24-cv-00151",
        content=BODY,
        published=datetime(2026, 7, 1, tzinfo=UTC),
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )


def _seed(tmp_path, monkeypatch, *, outcome=None, links=(DOCKET_LINK,)):
    """Raw + processed stores with enriched verdict-true recap rows."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [_recap(link, title=f"Case {i}") for i, link in enumerate(links)]
    )
    _install(monkeypatch, FakeEnricher(_reply(outcome=outcome)))
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "10")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    rows = JsonlProcessedStore(processed_path).load_all()
    assert all(r.forensics is not None and r.forensics.is_insider_case for r in rows)
    return raw_path, processed_path, JsonIngestState(tmp_path / "state.json")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _api(statuses: dict[int, dict], entries: dict[int, list[dict]], hits: list[str]):
    """Handler serving docket detail + entries-tail for the given fixtures."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        hits.append(path)
        if path.startswith("/api/rest/v4/dockets/"):
            docket_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            return httpx.Response(200, text=json.dumps(statuses[docket_id]))
        if path == "/api/rest/v4/docket-entries/":
            docket_id = int(request.url.params["docket"])
            return httpx.Response(
                200, text=json.dumps({"results": entries.get(docket_id, [])})
            )
        return httpx.Response(404, text="{}")

    return handler


def test_noop_when_disabled_by_default(tmp_path, monkeypatch) -> None:
    """DOCKET_FOLLOW_MAX_PER_RUN defaults to 0: the lane must not touch the network."""
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch)
    hits: list[str] = []
    with _client(_api({}, {}, hits)) as client:
        result = run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
        )
    assert hits == []
    assert result.total_articles_saved == 0


def test_candidate_filter(tmp_path, monkeypatch) -> None:
    """Only verdict-true recap rows lacking an outcome, minus done/recent."""
    done_link = "https://www.courtlistener.com/docket/222/acme-v-jones/"
    recent_link = "https://www.courtlistener.com/docket/333/acme-v-lee/"
    raw_path, processed_path, state = _seed(
        tmp_path, monkeypatch, links=(DOCKET_LINK, done_link, recent_link)
    )
    now = datetime.now(UTC)
    state.set(_DONE_KEY.format(link=done_link), f"terminated @ {now.isoformat()}")
    state.set(_ATTEMPT_KEY.format(link=recent_link), f"{_CHECKED_PREFIX}{now.isoformat()}")

    links = [
        r.link
        for r in select_follow_candidates(
            str(processed_path), state=state, now=now, repoll_days=7.0
        )
    ]
    assert links == [DOCKET_LINK]


def test_rows_with_outcome_are_not_followed(tmp_path, monkeypatch) -> None:
    _, processed_path, state = _seed(tmp_path, monkeypatch, outcome="charged under DTSA")
    assert (
        select_follow_candidates(
            str(processed_path), state=state, now=datetime.now(UTC), repoll_days=7.0
        )
        == []
    )


def test_quiet_docket_stamps_without_entries_fetch(tmp_path, monkeypatch) -> None:
    """No termination, no new filing date → one status call, no update, no clear."""
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch)
    hits: list[str] = []
    statuses = {
        111: {"date_filed": "2026-07-01", "date_terminated": None, "date_last_filing": None}
    }
    with _client(_api(statuses, {}, hits)) as client:
        run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            limit=5,
            request_delay=0,
        )
    assert hits == ["/api/rest/v4/dockets/111/"]
    assert (state.get(_ATTEMPT_KEY.format(link=DOCKET_LINK)) or "").startswith(_CHECKED_PREFIX)
    row = JsonlProcessedStore(str(processed_path)).load_all()[0]
    assert row.forensics is not None  # untouched


def test_termination_appends_updates_and_queues_reenrich(tmp_path, monkeypatch) -> None:
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch)
    original_raw = JsonlArticleStore(str(raw_path)).load_all()[0]
    hits: list[str] = []
    statuses = {
        111: {
            "date_filed": "2026-07-01",
            "date_terminated": "2026-08-01",
            "date_last_filing": "2026-08-01",
        }
    }
    entries = {
        111: [
            {"entry_number": 140, "date_filed": "2026-07-20", "description": "STATUS REPORT"},
            {
                "entry_number": 147,
                "date_filed": "2026-08-01",
                "description": "ORDER DISMISSING CASE WITH PREJUDICE per stipulation",
            },
        ]
    }
    with _client(_api(statuses, entries, hits)) as client:
        result = run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            limit=5,
            request_delay=0,
        )

    assert result.total_articles_saved == 1
    raw = JsonlArticleStore(str(raw_path)).load_all()[0]
    assert "=== DOCKET UPDATES as of" in raw.content
    assert "terminated (closed) this case on 2026-08-01" in raw.content
    assert "ORDER DISMISSING CASE" in raw.content
    assert "STATUS REPORT" not in raw.content  # non-outcome entries stay out
    assert raw.content.startswith(BODY.strip())  # original body preserved
    assert raw.ingested_at > original_raw.ingested_at  # re-processing trigger

    row = JsonlProcessedStore(str(processed_path)).load_all()[0]
    assert row.forensics is None and row.ai_summary is None  # cleared for re-enrich
    assert len(row.enrichment_history) >= 1  # archive preserved

    assert (state.get(_DONE_KEY.format(link=DOCKET_LINK)) or "").startswith("terminated @ ")
    assert state.get(_FILING_KEY.format(link=DOCKET_LINK)) == "2026-08-01"


def test_update_block_is_replaced_not_stacked(tmp_path, monkeypatch) -> None:
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch)
    statuses = {
        111: {
            "date_filed": "2026-07-01",
            "date_terminated": None,
            "date_last_filing": "2026-08-01",
        }
    }
    entries = {
        111: [
            {
                "entry_number": 90,
                "date_filed": "2026-08-01",
                "description": "PARTIAL SUMMARY JUDGMENT order",
            }
        ]
    }
    with _client(_api(statuses, entries, [])) as client:
        run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            limit=5,
            request_delay=0,
        )
    # The cleared row re-enriches on the next processing run (same-cycle in
    # production); only then is it a follow candidate again.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    row = JsonlProcessedStore(str(processed_path)).load_all()[0]
    # (history stays deduped: an identical re-generation is skipped by signature)
    assert row.forensics is not None

    # Second pass: docket moved again; the old block must be replaced.
    state.set(
        _ATTEMPT_KEY.format(link=DOCKET_LINK), f"{_CHECKED_PREFIX}2020-01-01T00:00:00+00:00"
    )
    statuses[111]["date_last_filing"] = "2026-08-10"
    entries[111] = [
        {
            "entry_number": 95,
            "date_filed": "2026-08-10",
            "description": "PERMANENT INJUNCTION granted",
        }
    ]
    with _client(_api(statuses, entries, [])) as client:
        run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            limit=5,
            request_delay=0,
        )

    raw = JsonlArticleStore(str(raw_path)).load_all()[0]
    assert raw.content.count("=== DOCKET UPDATES") == 1
    assert "PERMANENT INJUNCTION" in raw.content


def test_throttle_aborts_sweep(tmp_path, monkeypatch) -> None:
    second = "https://www.courtlistener.com/docket/222/acme-v-jones/"
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch, links=(DOCKET_LINK, second))
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        return httpx.Response(429, text="Expected available in 30 seconds.")

    with _client(handler) as client:
        result = run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            limit=5,
            request_delay=0,  # disables wait-and-retry: first 429 stops the sweep
        )
    assert len(hits) == 1
    assert result.total_articles_saved == 0


def test_dry_run_lists_without_network_or_writes(tmp_path, monkeypatch, capsys) -> None:
    raw_path, processed_path, state = _seed(tmp_path, monkeypatch)
    hits: list[str] = []
    with _client(_api({}, {}, hits)) as client:
        run_docket_follow(
            store_path=str(raw_path),
            processed_path=str(processed_path),
            state=state,
            client=client,
            dry_run=True,
        )
    assert hits == []
    assert "would poll docket 111" in capsys.readouterr().out
    assert JsonlProcessedStore(str(processed_path)).load_all()[0].forensics is not None


def _record(*, methods: int, outcome: str | None, summary: str = "note") -> EnrichmentRecord:
    return EnrichmentRecord(
        ai_summary=summary,
        forensics=PerCaseForensics(
            link="https://ex.com/case",
            title="Acme v. Smith",
            is_insider_case=True,
            confidence=0.9,
            outcome=outcome,
            methods=[{"action": f"m{i}"} for i in range(methods)],
            extracted_at=datetime.now(UTC),
        ),
    )


def test_outcome_bearing_record_survives_one_method_deficit() -> None:
    """A follow-up that learns the outcome must not lose for knowing less procedure."""
    complaint_stage = _record(methods=2, outcome=None)
    with_outcome = _record(methods=1, outcome="dismissed with prejudice (settled)")
    best = select_best_enrichment([complaint_stage, with_outcome])
    assert best is not None and best.forensics.outcome


def test_outcome_bonus_cannot_rescue_a_gutted_record() -> None:
    rich = _record(methods=4, outcome=None)
    gutted = _record(methods=0, outcome="dismissed", summary="")
    best = select_best_enrichment([rich, gutted])
    assert best is not None and best.forensics.outcome is None
