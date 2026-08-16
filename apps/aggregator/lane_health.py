"""Per-lane ingest health telemetry — a dynamic smoke test of every source.

Every refresh cycle records one row per source lane. Lanes are enumerated
from the LIVE configuration (feed registry, CourtListener search types,
web-keyword URLs, publication catalog, social subscriptions, …), never from
a hardcoded list — adding or removing a source automatically adds or removes
its health row. Rows the run actually produced (including dynamic lanes such
as courtlistener-history) are recorded too, so nothing that ran goes
unmeasured.

Persisted at ``data/state/lane_health.json`` (job-written under ``state/``,
API reads it — same contract as ``technique_hunts.json``). The API serves it
at ``GET /lanes/health`` and the boot snapshot embeds the summary so the UI
footer can show "data sources: X healthy / Y broken".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shared.schemas import FeedSource, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_LANE_HEALTH_PATH = "data/state/lane_health.json"

# A lane is BROKEN after this many consecutive cycles that errored or came
# back empty. 3 cycles ≈ 18h at the 6h refresh cadence — long enough to ride
# out a one-off rate limit or a quiet news window, short enough to surface a
# dead feed within a day.
BROKEN_AFTER_CYCLES = 3

# Outcomes that advance the consecutive-failure counter. "skipped" (lane is
# configured but produced no row this cycle — e.g. the X cadence guard)
# carries the counter forward unchanged.
_FAILURE_OUTCOMES = frozenset({"http_error", "parse_error", "error", "empty"})

# Greppable end-of-cycle markers (operator ask: "a way to call out if
# something is broken").
BROKEN_MARKER = "[LANE-BROKEN]"
HEALTH_MARKER = "[LANE-HEALTH]"


@dataclass(frozen=True)
class ExpectedLane:
    """One configured source lane that should report each cycle."""

    id: str
    name: str
    kind: str  # rss | court | feedly | web-keyword | datatheftnews | publications | social


def expected_lane_specs(
    *,
    feeds: list[FeedSource] | None = None,
    include_feedly: bool = True,
    include_courtlistener: bool = True,
    include_web_keywords: bool = True,
    include_datatheftnews: bool = True,
    include_social: bool = True,
    include_publications: bool = True,
) -> list[ExpectedLane]:
    """Enumerate every lane the current configuration says should run.

    Mirrors the lane gates in ``run_full_pipeline`` (skip flags and the
    optional-credential checks each lane applies), reading the same live
    registries the lanes themselves read.
    """
    from apps.aggregator.config import get_enabled_feeds

    settings = get_settings()
    lanes: list[ExpectedLane] = []

    for feed in get_enabled_feeds(feeds):
        lanes.append(ExpectedLane(id=feed.id, name=feed.name, kind="rss"))

    if include_feedly and (settings.feedly_access_token or "").strip():
        for stream_id in settings.feedly_stream_id_list():
            lanes.append(
                ExpectedLane(id=f"feedly:{stream_id}", name=f"Feedly {stream_id}", kind="feedly")
            )

    if include_courtlistener:
        from apps.aggregator.courtlistener import SEARCH_TYPES, parse_types

        try:
            type_list = parse_types(settings.courtlistener_types)
        except ValueError:
            type_list = ["dockets"]
        for search_type in type_list:
            spec = SEARCH_TYPES[search_type]
            lanes.append(ExpectedLane(id=spec.source_id, name=spec.source_name, kind="court"))

    if include_web_keywords:
        from urllib.parse import urlparse

        from apps.aggregator.web_keywords import SOURCE_NAME as WEB_SOURCE_NAME

        for feed_url in settings.web_keyword_feed_url_list():
            host = urlparse(feed_url).netloc or "feed"
            lanes.append(
                ExpectedLane(id=f"web-keyword:{host}", name=WEB_SOURCE_NAME, kind="web-keyword")
            )

    if include_datatheftnews:
        from apps.aggregator.datatheftnews import SOURCE_ID, SOURCE_NAME

        lanes.append(ExpectedLane(id=SOURCE_ID, name=SOURCE_NAME, kind="datatheftnews"))

    if include_social and settings.social_ingest_enabled:
        from apps.aggregator.reddit import subreddit_source
        from apps.aggregator.reddit_pipeline import resolve_subreddits
        from apps.aggregator.x_client import handle_source
        from apps.aggregator.x_pipeline import resolve_handles

        for sub in resolve_subreddits():
            source_id, source_name = subreddit_source(sub)
            lanes.append(ExpectedLane(id=source_id, name=source_name, kind="social"))
        # X rows only when read credentials exist; without them the pipeline
        # skips wholesale (no per-handle rows to expect).
        if settings.x_bearer_token or (settings.x_consumer_key and settings.x_consumer_secret):
            for handle in resolve_handles():
                source_id, source_name = handle_source(handle)
                lanes.append(ExpectedLane(id=source_id, name=source_name, kind="social"))

    if include_publications:
        from apps.aggregator.publication_sources import get_publication_sources

        for source in get_publication_sources():
            lanes.append(ExpectedLane(id=source.id, name=source.name, kind="publications"))

    # De-dupe on id, first spec wins (e.g. an RSS feed and a pipeline lane
    # sharing an id must not produce two rows).
    seen: set[str] = set()
    unique: list[ExpectedLane] = []
    for lane in lanes:
        if lane.id in seen:
            continue
        seen.add(lane.id)
        unique.append(lane)
    return unique


def _classify_outcome(success: bool, articles_fetched: int, error: str | None) -> str:
    if success:
        return "ok" if articles_fetched > 0 else "empty"
    err = (error or "").lower()
    if "http " in err or "status" in err:
        return "http_error"
    if "parse" in err:
        return "parse_error"
    return "error"


def _infer_kind(source_id: str) -> str:
    """Best-effort kind for dynamic result rows outside the expected set."""
    if source_id.startswith("courtlistener") or source_id.startswith("pacer"):
        return "court"
    if source_id.startswith("feedly:"):
        return "feedly"
    if source_id.startswith("web-keyword"):
        return "web-keyword"
    if source_id.startswith("pub-"):
        return "publications"
    if source_id.startswith("social-") or source_id.startswith("reddit-"):
        return "social"
    if source_id == "datatheftnews":
        return "datatheftnews"
    return "other"


def _empty_payload() -> dict:
    return {
        "generated_at": None,
        "broken_after_cycles": BROKEN_AFTER_CYCLES,
        "lanes": {},
        "summary": {"total": 0, "healthy": 0, "broken": 0, "broken_lanes": []},
    }


def read_lane_health(path: str | Path | None = None) -> dict:
    """Read the persisted health file; missing/corrupt resets to empty."""
    file_path = Path(path or get_settings().lane_health_path)
    if not file_path.exists():
        return _empty_payload()
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable lane health %s: %s", file_path, exc)
        return _empty_payload()
    if not isinstance(payload, dict) or not isinstance(payload.get("lanes"), dict):
        logger.warning("Ignoring malformed lane health %s", file_path)
        return _empty_payload()
    return payload


def record_lane_health(
    results: Iterable[SourceIngestionResult],
    expected: Iterable[ExpectedLane],
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Fold this cycle's per-source outcomes into the persisted health file.

    One row per expected lane (configured lanes with no result row this cycle
    are recorded as "skipped") plus one row per result outside the expected
    set. Consecutive-failure counts carry forward from the previous cycle;
    rows for lanes that are neither configured nor present in the results are
    dropped, so removing a source removes its row.
    """
    file_path = Path(path or get_settings().lane_health_path)
    stamp = (now or datetime.now(UTC)).isoformat()
    previous = read_lane_health(file_path)["lanes"]

    # Aggregate results per source id — some lanes legally emit several rows
    # per cycle (merged CourtListener parts, repeated web-keyword hosts).
    merged: dict[str, dict] = {}
    for res in results:
        row = merged.setdefault(
            res.source_id,
            {
                "name": res.source_name,
                "success": False,
                "any_result": False,
                "fetched": 0,
                "new": 0,
                "errors": [],
            },
        )
        row["any_result"] = True
        row["success"] = row["success"] or res.success
        row["fetched"] += res.articles_fetched
        row["new"] += res.articles_saved
        if res.error:
            row["errors"].append(res.error)

    expected_list = list(expected)
    known_ids = {lane.id for lane in expected_list}
    lanes: dict[str, dict] = {}

    def build_row(lane_id: str, name: str, kind: str, agg: dict | None) -> dict:
        prev = previous.get(lane_id) or {}
        prev_failures = int(prev.get("consecutive_failures") or 0)
        if agg is None:
            outcome = "skipped"
            error = None
            fetched = 0
            new = 0
            failures = prev_failures  # carry forward unchanged
            last_ok = prev.get("last_ok_at")
        else:
            error = "; ".join(agg["errors"]) or None
            outcome = _classify_outcome(agg["success"], agg["fetched"], error)
            fetched = agg["fetched"]
            new = agg["new"]
            failures = prev_failures + 1 if outcome in _FAILURE_OUTCOMES else 0
            last_ok = stamp if outcome == "ok" else prev.get("last_ok_at")
        return {
            "id": lane_id,
            "name": name,
            "kind": kind,
            "outcome": outcome,
            "error": error if outcome in _FAILURE_OUTCOMES else None,
            "articles_fetched": fetched,
            "articles_new": new,
            "last_run_at": stamp if agg is not None else prev.get("last_run_at"),
            "last_ok_at": last_ok,
            "consecutive_failures": failures,
            "broken": failures >= BROKEN_AFTER_CYCLES,
        }

    for lane in expected_list:
        agg = merged.get(lane.id)
        name = (agg or {}).get("name") or lane.name
        lanes[lane.id] = build_row(lane.id, name, lane.kind, agg)
    for source_id, agg in merged.items():
        if source_id in known_ids:
            continue
        lanes[source_id] = build_row(source_id, agg["name"], _infer_kind(source_id), agg)

    broken_lanes = sorted(lane_id for lane_id, row in lanes.items() if row["broken"])
    payload = {
        "generated_at": stamp,
        "broken_after_cycles": BROKEN_AFTER_CYCLES,
        "lanes": lanes,
        "summary": {
            "total": len(lanes),
            "healthy": len(lanes) - len(broken_lanes),
            "broken": len(broken_lanes),
            "broken_lanes": broken_lanes,
        },
    }

    # Atomic write, same idiom as JsonIngestState (tmp + replace).
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(file_path)
    return payload


def summarize_lane_health(payload: dict) -> list[str]:
    """End-of-cycle log lines: one roll-up, plus one loud broken call-out."""
    lanes = payload.get("lanes") or {}
    counts: dict[str, int] = {}
    for row in lanes.values():
        counts[row.get("outcome") or "?"] = counts.get(row.get("outcome") or "?", 0) + 1
    lines = [
        f"{HEALTH_MARKER} lanes={len(lanes)} "
        f"ok={counts.get('ok', 0)} empty={counts.get('empty', 0)} "
        f"http_error={counts.get('http_error', 0)} parse_error={counts.get('parse_error', 0)} "
        f"error={counts.get('error', 0)} skipped={counts.get('skipped', 0)} "
        f"broken={payload.get('summary', {}).get('broken', 0)}"
    ]
    broken = [lanes[lane_id] for lane_id in payload.get("summary", {}).get("broken_lanes", [])]
    if broken:
        details = "; ".join(
            f"{row['id']} ({row['kind']}, {row['consecutive_failures']} cycles, "
            f"{row['outcome']}{': ' + row['error'] if row.get('error') else ''})"
            for row in broken
        )
        lines.append(
            f"{BROKEN_MARKER} {len(broken)} lane(s) failing >= "
            f"{payload.get('broken_after_cycles', BROKEN_AFTER_CYCLES)} consecutive cycles: "
            f"{details}"
        )
    return lines


def log_lane_health(payload: dict) -> None:
    for line in summarize_lane_health(payload):
        if line.startswith(BROKEN_MARKER):
            logger.error("%s", line)
        else:
            logger.info("%s", line)
