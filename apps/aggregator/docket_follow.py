"""Follow open dockets of adjudicated insider cases until an outcome lands.

Enrichment runs once, usually at the complaint stage, so ``forensics.outcome``
stays null even after the court rules — the incremental search watermark
filters on filing date, so a docket that later terminates never re-surfaces
through ingestion. This lane polls the CourtListener docket API for
verdict-true RECAP cases that still lack an outcome. When a docket terminates
or gains outcome-bearing entries (judgment, dismissal, settlement,
injunction, …), the new entry text is appended to the raw article with a
fresh ``ingested_at`` and the processed row's selected LLM fields are cleared
— the same-cycle processing run then re-enriches over complaint + updates,
exactly like the text backfill (the precedent this copies).

Off by default (DOCKET_FOLLOW_MAX_PER_RUN=0): the GCP rollback job runs the
same ``all`` entrypoint against the same bucket and must not double-write.
Enable in .env.spark only.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta

import httpx

from apps.aggregator.courtlistener import (
    SOURCE_ID,
    CourtListenerError,
    fetch_docket_entries_tail,
    fetch_docket_status,
    parse_docket_id,
)
from apps.aggregator.courtlistener_pipeline import (
    _MAX_THROTTLE_WAITS,
    _clear_llm_fields,
    _is_throttled,
    _throttle_wait_seconds,
)
from apps.aggregator.ingest_state import DEFAULT_STATE_PATH, JsonIngestState
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PROCESSED_PATH = "data/processed/articles.jsonl"

_ATTEMPT_KEY = "docket_follow:{link}"  # "checked @ <ISO>" — repoll clock
_DONE_KEY = "docket_follow_done:{link}"  # "terminated @ <ISO>" — never repoll
_FILING_KEY = "docket_follow_filing:{link}"  # last seen date_last_filing
_CHECKED_PREFIX = "checked @ "

# One consolidated update block per article, replaced (never stacked) on each
# follow-up so repeated updates cannot push the outcome out of the enricher's
# kept-tail input slice.
_UPDATE_HEADER_RE = re.compile(r"\n=== DOCKET UPDATES as of [^=]*===\n.*\Z", re.S)
_UPDATE_HEADER_FMT = "=== DOCKET UPDATES as of {date} ==="

# Entry descriptions that can carry a case outcome. Deliberately generous —
# the LLM adjudicates what the entries mean; this only gates the append.
OUTCOME_ENTRY_RE = re.compile(
    r"judgment|dismiss|settle|stipulat|consent|injunction|default|verdict"
    r"|sentenc|plea|terminat|closed|sanction|damages|remand",
    re.I,
)

_MAX_ENTRIES_PER_UPDATE = 12
_MAX_ENTRY_DESC_CHARS = 300


def _attempted_recently(
    state: JsonIngestState, link: str, now: datetime, repoll_days: float
) -> bool:
    stored = state.get(_ATTEMPT_KEY.format(link=link))
    if not stored or not stored.startswith(_CHECKED_PREFIX):
        return False
    try:
        attempted = datetime.fromisoformat(stored[len(_CHECKED_PREFIX) :])
    except ValueError:
        return False
    if attempted.tzinfo is None:
        attempted = attempted.replace(tzinfo=UTC)
    return (now - attempted) < timedelta(days=repoll_days)


def select_follow_candidates(
    processed_path: str,
    *,
    state: JsonIngestState,
    now: datetime,
    repoll_days: float,
) -> list:
    """Verdict-true RECAP rows still lacking an outcome, least-recently polled first."""
    store = JsonlProcessedStore(processed_path)
    rows = []
    for row in store.load_all():
        if row.source_id != SOURCE_ID:
            continue
        forensics = getattr(row, "forensics", None)
        if forensics is None or not forensics.is_insider_case:
            continue
        if (forensics.outcome or "").strip():
            continue
        if parse_docket_id(row.link) is None:
            continue
        if state.get(_DONE_KEY.format(link=row.link)):
            continue
        if _attempted_recently(state, row.link, now, repoll_days):
            continue
        rows.append(row)

    def _last_attempt(row) -> str:
        return state.get(_ATTEMPT_KEY.format(link=row.link)) or ""

    rows.sort(key=_last_attempt)  # never-polled ("") first, then oldest stamp
    return rows


def _strip_update_block(content: str) -> str:
    return _UPDATE_HEADER_RE.sub("", content or "").rstrip()


def _build_update_block(
    *, now: datetime, terminated: str | None, entries: list[dict]
) -> str:
    lines = [_UPDATE_HEADER_FMT.format(date=now.date().isoformat())]
    if terminated:
        lines.append(f"The court terminated (closed) this case on {terminated}.")
    for entry in entries[-_MAX_ENTRIES_PER_UPDATE:]:
        desc = " ".join((entry.get("description") or "").split())[:_MAX_ENTRY_DESC_CHARS]
        if not desc:
            continue
        number = entry.get("entry_number")
        tag = f"#{number}" if number else "-"
        lines.append(f"[{entry.get('date_filed') or '?'}] {tag}: {desc}")
    return "\n".join(lines)


def run_docket_follow(
    *,
    token: str | None = None,
    limit: int | None = None,
    repoll_days: float | None = None,
    state: JsonIngestState | None = None,
    state_path: str = DEFAULT_STATE_PATH,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    processed_path: str | None = None,
    request_delay: float | None = None,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> IngestionRunResult:
    """Poll dockets of outcome-less verdict-true cases; queue changed ones to re-enrich."""
    settings = get_settings()
    api_token = token if token is not None else settings.courtlistener_api_token
    polls_allowed = limit if limit is not None else settings.docket_follow_max_per_run
    window_days = repoll_days if repoll_days is not None else settings.docket_follow_repoll_days
    delay = (
        request_delay if request_delay is not None else settings.courtlistener_request_delay_seconds
    )
    processed = processed_path or DEFAULT_PROCESSED_PATH

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    if polls_allowed <= 0 and not dry_run:
        result.finished_at = datetime.now(UTC)
        return result

    ingest_state = state or JsonIngestState(state_path)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    candidates = select_follow_candidates(
        processed, state=ingest_state, now=started_at, repoll_days=window_days
    )
    if polls_allowed > 0:
        candidates = candidates[:polls_allowed]

    if dry_run:
        for row in candidates:
            print(f"would poll docket {parse_docket_id(row.link)}: {row.title[:80]}")
        print(f"docket follow dry run: {len(candidates)} candidate(s)")
        result.finished_at = datetime.now(UTC)
        return result

    raw_by_link = {a.link: a for a in article_store.load_all()}
    polled = 0
    updated_articles = []
    errors: list[str] = []
    throttle_waits = 0

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        for row in candidates:
            docket_id = parse_docket_id(row.link)
            raw = raw_by_link.get(row.link)
            if docket_id is None or raw is None:
                continue
            if polled > 0 and delay > 0:
                time.sleep(delay)  # account-wide 10/min throttle
            polled += 1
            try:
                status = fetch_docket_status(docket_id, token=api_token, client=http)
            except CourtListenerError as exc:
                if _is_throttled(exc) and throttle_waits < _MAX_THROTTLE_WAITS and delay > 0:
                    wait = _throttle_wait_seconds(exc)
                    throttle_waits += 1
                    logger.warning("CourtListener throttled — waiting %.0fs, then retrying", wait)
                    time.sleep(wait)
                    try:
                        status = fetch_docket_status(docket_id, token=api_token, client=http)
                    except CourtListenerError as exc2:
                        errors.append(f"{row.link}: {exc2}")
                        if _is_throttled(exc2):
                            logger.warning("Still throttled — stopping this sweep")
                            break
                        continue
                else:
                    errors.append(f"{row.link}: {exc}")
                    if _is_throttled(exc):
                        logger.warning("CourtListener throttled (429) — stopping this sweep")
                        break
                    continue

            ingest_state.set(
                _ATTEMPT_KEY.format(link=row.link),
                f"{_CHECKED_PREFIX}{started_at.isoformat()}",
            )
            terminated = status.get("date_terminated")
            last_filing = status.get("date_last_filing") or ""
            prev_filing = ingest_state.get(_FILING_KEY.format(link=row.link)) or ""

            if not terminated and (not last_filing or last_filing == prev_filing):
                ingest_state.set(_FILING_KEY.format(link=row.link), last_filing)
                continue

            if delay > 0:
                time.sleep(delay)
            try:
                entries = fetch_docket_entries_tail(
                    docket_id,
                    token=api_token,
                    date_filed_after=prev_filing or None,
                    client=http,
                )
            except CourtListenerError as exc:
                errors.append(f"{row.link}: {exc}")
                if _is_throttled(exc):
                    logger.warning("CourtListener throttled (429) — stopping this sweep")
                    break
                continue

            outcome_entries = [
                e for e in entries if OUTCOME_ENTRY_RE.search(e.get("description") or "")
            ]
            ingest_state.set(_FILING_KEY.format(link=row.link), last_filing)
            if terminated:
                # The docket is final: mark done either way so a text-less
                # termination (no RECAP descriptions) is not re-polled forever.
                ingest_state.set(
                    _DONE_KEY.format(link=row.link),
                    f"terminated @ {started_at.isoformat()}",
                )
            if not outcome_entries and not terminated:
                continue

            block = _build_update_block(
                now=started_at, terminated=terminated, entries=outcome_entries
            )
            base = _strip_update_block(raw.content or "")
            combined = f"{base}\n{block}" if base else block
            updated_articles.append(
                raw.model_copy(
                    update={"content": combined, "ingested_at": datetime.now(UTC)}
                )
            )
    finally:
        if own_client:
            http.close()

    saved = 0
    if updated_articles:
        refresh = getattr(article_store, "refresh", None)
        if callable(refresh):
            new, changed = refresh(updated_articles, force=True)
            saved = new + changed
        else:
            saved = article_store.save(updated_articles)
        _clear_llm_fields(processed, {a.link for a in updated_articles})

    # A caught-up lane is healthy-idle, not broken: when every tracked docket
    # sits inside its repoll window there is nothing to attempt, and reporting
    # fetched=0 here made lane health count "empty" cycles toward BROKEN
    # (false alarm observed after 3 quiet cycles, 2026-08-21). Report only
    # when the lane actually did something — polled, saved, or errored.
    if polled or saved or errors:
        result.sources.append(
            SourceIngestionResult(
                source_id="courtlistener-docket-follow",
                source_name="CourtListener docket follow",
                success=not (errors and saved == 0 and polled == len(errors)),
                articles_fetched=polled,
                articles_saved=saved,
                error="; ".join(errors[:5]) if errors and saved == 0 else None,
            )
        )
    result.total_articles_saved = saved
    result.finished_at = datetime.now(UTC)
    logger.info(
        "Docket follow: polled=%d updated=%d errors=%d", polled, saved, len(errors)
    )
    return result
