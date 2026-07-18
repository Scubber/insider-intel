"""Budget-capped PACER purchasing via CourtListener's RECAP Fetch API.

The free-archive text backfill (courtlistener_pipeline) can only harvest
documents someone already bought. This module buys the missing lead documents
for insider-qualifying cases — strictly gated:

- Requires PACER credentials AND a CourtListener API token (else no-op).
- Only cases the pipeline classified as insider-relevant (ITM hits / use case).
- Only after the free archive was checked and came up empty.
- Bounded per run (PACER_PURCHASE_MAX_PER_RUN) and per quarter
  (PACER_QUARTERLY_BUDGET_CENTS, default $27 — under PACER's $30/quarter fee
  waiver, so typical usage costs nothing).

Purchases land in the public RECAP archive (public court records — every
purchase also enriches the commons); the existing text backfill pulls the
OCR'd text on a later refresh.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from apps.aggregator.courtlistener import (
    FETCH_TYPE_DOCKET,
    FETCH_TYPE_PDF,
    SOURCE_ID,
    CourtListenerError,
    fetch_docket_entries,
    parse_docket_id,
    request_pacer_fetch,
)
from apps.aggregator.courtlistener_pipeline import (
    _TEXT_ATTEMPT_KEY,
    _is_throttled,
    needs_full_text,
)
from apps.aggregator.ingest_state import DEFAULT_STATE_PATH, JsonIngestState
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.process_pipeline import DEFAULT_PROCESSED_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.agents.summarize import article_qualifies
from shared.schemas import IngestionRunResult, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

# Conservative per-purchase estimate: PACER caps one document at $3.00.
ESTIMATED_COST_CENTS = 300

_SPEND_KEY = "pacer_spend:{quarter}"
_PURCHASE_KEY = "pacer_purchase:{link}"


@dataclass
class PlannedPurchase:
    link: str
    title: str
    stage: str  # "docket" (report) | "doc" (lead PDF)
    estimated_cents: int = ESTIMATED_COST_CENTS


@dataclass
class PurchasePlan:
    purchases: list[PlannedPurchase] = field(default_factory=list)


def quarter_key(now: datetime) -> str:
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


def _spent_cents(state: JsonIngestState, quarter: str) -> int:
    raw = state.get(_SPEND_KEY.format(quarter=quarter))
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def _purchase_stage(state: JsonIngestState, link: str) -> str | None:
    """Last purchase stage recorded for a link ("docket" / "doc"), or None."""
    stored = state.get(_PURCHASE_KEY.format(link=link))
    if not stored:
        return None
    return stored.split("@", 1)[0].strip() or None


def run_pacer_purchases(
    *,
    token: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    state: JsonIngestState | None = None,
    state_path: str = DEFAULT_STATE_PATH,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    processed_path: str = DEFAULT_PROCESSED_PATH,
    client: httpx.Client | None = None,
) -> tuple[IngestionRunResult, PurchasePlan]:
    """Buy missing lead documents for qualifying cases, within budget.

    Returns (run result, plan). With ``dry_run=True`` nothing is bought — the
    plan lists what would be.
    """
    settings = get_settings()
    cl_token = token if token is not None else settings.courtlistener_api_token
    per_run = limit if limit is not None else settings.pacer_purchase_max_per_run
    budget_cents = settings.pacer_quarterly_budget_cents

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    plan = PurchasePlan()

    def finish(*, fetched: int = 0, saved: int = 0, error: str | None = None, record: bool = True):
        if record:
            result.sources.append(
                SourceIngestionResult(
                    source_id="pacer-fetch",
                    source_name="PACER purchases (RECAP Fetch)",
                    success=error is None,
                    articles_fetched=fetched,
                    articles_saved=saved,
                    error=error,
                )
            )
        result.total_articles_saved = saved
        result.finished_at = datetime.now(UTC)
        return result, plan

    if not (settings.pacer_username and settings.pacer_password):
        return finish(record=False)  # feature dark — no credentials, no purchases
    if per_run <= 0 or budget_cents <= 0:
        return finish(record=False)
    if not (cl_token and cl_token.strip()):
        logger.warning(
            "PACER credentials set but COURTLISTENER_API_TOKEN missing — "
            "the RECAP Fetch API requires it; skipping purchases"
        )
        return finish(record=False)

    ingest_state = state or JsonIngestState(state_path)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    quarter = quarter_key(started_at)
    spent = _spent_cents(ingest_state, quarter)

    qualifying_links = {
        row.link for row in JsonlProcessedStore(processed_path).load_all() if article_qualifies(row)
    }

    candidates = [
        a
        for a in article_store.load_all()
        if a.source_id == SOURCE_ID
        and needs_full_text(a)
        and a.link in qualifying_links
        # Buy only after the free archive was checked and came up empty.
        and ingest_state.get(_TEXT_ATTEMPT_KEY.format(link=a.link)) is not None
    ]
    candidates.sort(
        key=lambda a: (a.published or a.ingested_at).timestamp()
        if (a.published or a.ingested_at)
        else 0.0,
        reverse=True,
    )

    purchased = 0
    errors: list[str] = []
    attempted = 0
    delay = settings.courtlistener_request_delay_seconds
    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        for article in candidates:
            if purchased >= per_run:
                break
            if spent + ESTIMATED_COST_CENTS > budget_cents:
                logger.info(
                    "PACER quarterly budget reached (%d/%d cents) — stopping",
                    spent,
                    budget_cents,
                )
                break
            docket_id = parse_docket_id(article.link)
            if docket_id is None:
                continue
            prior_stage = _purchase_stage(ingest_state, article.link)
            if attempted > 0 and delay > 0:
                time.sleep(delay)  # burst politeness — CourtListener 429s rapid fire
            attempted += 1

            try:
                entries = fetch_docket_entries(docket_id, token=cl_token, client=http)
            except CourtListenerError as exc:
                errors.append(f"{article.link}: {exc}")
                if _is_throttled(exc):
                    logger.warning("CourtListener throttled (429) — stopping purchases this run")
                    break
                continue

            lead_doc = None
            for entry in entries:
                docs = entry.get("recap_documents") or []
                if docs:
                    lead_doc = docs[0]
                    break

            if lead_doc is not None and lead_doc.get("is_available"):
                # The archive filled in since our last check — no purchase
                # needed; let the next backfill pull the text immediately.
                _clear_attempt(ingest_state, article.link)
                continue

            if lead_doc is None:
                stage = "docket"
                if prior_stage == "docket":
                    continue  # report already bought; entries not ingested yet
                fetch_kwargs = {"request_type": FETCH_TYPE_DOCKET, "docket": docket_id}
            else:
                stage = "doc"
                if prior_stage == "doc":
                    continue  # already bought; awaiting archive processing
                fetch_kwargs = {
                    "request_type": FETCH_TYPE_PDF,
                    "recap_document": int(lead_doc["id"]),
                }

            plan.purchases.append(
                PlannedPurchase(link=article.link, title=article.title, stage=stage)
            )
            if dry_run:
                # Bound the plan by the same caps a real run would honor.
                purchased += 1
                spent += ESTIMATED_COST_CENTS
                continue

            try:
                request_pacer_fetch(
                    token=cl_token,
                    pacer_username=settings.pacer_username,
                    pacer_password=settings.pacer_password,
                    client=http,
                    **fetch_kwargs,
                )
            except CourtListenerError as exc:
                errors.append(f"{article.link}: {exc}")
                plan.purchases.pop()
                continue

            purchased += 1
            spent += ESTIMATED_COST_CENTS
            ingest_state.set(_SPEND_KEY.format(quarter=quarter), str(spent))
            ingest_state.set(
                _PURCHASE_KEY.format(link=article.link),
                f"{stage} @ {started_at.isoformat()}",
            )
            _clear_attempt(ingest_state, article.link)
            logger.info(
                "PACER purchase queued (%s) for %s — est. spend %d/%d cents this quarter",
                stage,
                article.link,
                spent,
                budget_cents,
            )
    finally:
        if own_client:
            http.close()

    return finish(
        fetched=attempted,
        saved=0 if dry_run else purchased,
        error="; ".join(errors[:5]) if errors and purchased == 0 and attempted else None,
    )


def _clear_attempt(state: JsonIngestState, link: str) -> None:
    """Let the next text backfill retry this link immediately."""
    # JsonIngestState has no delete; an epoch value falls outside the
    # retry window so _attempted_recently returns False.
    state.set(_TEXT_ATTEMPT_KEY.format(link=link), "1970-01-01T00:00:00+00:00")
