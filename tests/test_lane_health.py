"""Lane-health telemetry: dynamic per-source smoke tests + broken call-outs."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.aggregator.lane_health import (
    BROKEN_AFTER_CYCLES,
    BROKEN_MARKER,
    HEALTH_MARKER,
    ExpectedLane,
    expected_lane_specs,
    read_lane_health,
    record_lane_health,
    summarize_lane_health,
)
from shared.schemas import FeedSource, SourceIngestionResult


def _res(
    source_id: str,
    *,
    success: bool = True,
    fetched: int = 0,
    saved: int = 0,
    error: str | None = None,
) -> SourceIngestionResult:
    return SourceIngestionResult(
        source_id=source_id,
        source_name=source_id,
        success=success,
        articles_fetched=fetched,
        articles_saved=saved,
        error=error,
    )


def _lane(lane_id: str, kind: str = "rss") -> ExpectedLane:
    return ExpectedLane(id=lane_id, name=lane_id, kind=kind)


def test_outcome_classification(tmp_path) -> None:
    path = tmp_path / "lane_health.json"
    payload = record_lane_health(
        [
            _res("ok-lane", fetched=5, saved=2),
            _res("empty-lane", fetched=0),
            _res("http-lane", success=False, error="https://x.example/feed: HTTP 404"),
            _res("parse-lane", success=False, error="parse-lane: Failed to parse feed"),
            _res("other-lane", success=False, error="boom"),
        ],
        [_lane(i) for i in ("ok-lane", "empty-lane", "http-lane", "parse-lane", "other-lane")],
        path=path,
    )
    lanes = payload["lanes"]
    assert lanes["ok-lane"]["outcome"] == "ok"
    assert lanes["ok-lane"]["articles_fetched"] == 5
    assert lanes["ok-lane"]["articles_new"] == 2
    assert lanes["ok-lane"]["consecutive_failures"] == 0
    assert lanes["ok-lane"]["last_ok_at"] == payload["generated_at"]
    assert lanes["empty-lane"]["outcome"] == "empty"
    assert lanes["http-lane"]["outcome"] == "http_error"
    assert lanes["http-lane"]["error"] == "https://x.example/feed: HTTP 404"
    assert lanes["parse-lane"]["outcome"] == "parse_error"
    assert lanes["other-lane"]["outcome"] == "error"
    for lane_id in ("empty-lane", "http-lane", "parse-lane", "other-lane"):
        assert lanes[lane_id]["consecutive_failures"] == 1
        assert lanes[lane_id]["broken"] is False
    # Round-trips through the reader.
    assert read_lane_health(path)["lanes"].keys() == lanes.keys()


def test_consecutive_failures_accumulate_to_broken(tmp_path) -> None:
    path = tmp_path / "lane_health.json"
    expected = [_lane("dead-feed")]
    for cycle in range(BROKEN_AFTER_CYCLES):
        payload = record_lane_health(
            [_res("dead-feed", success=False, error="HTTP 404")], expected, path=path
        )
        row = payload["lanes"]["dead-feed"]
        assert row["consecutive_failures"] == cycle + 1
    assert row["broken"] is True
    assert payload["summary"]["broken_lanes"] == ["dead-feed"]
    assert payload["summary"]["broken"] == 1
    assert payload["summary"]["healthy"] == 0


def test_success_resets_counter(tmp_path) -> None:
    path = tmp_path / "lane_health.json"
    expected = [_lane("flaky")]
    for _ in range(BROKEN_AFTER_CYCLES):
        record_lane_health([_res("flaky", success=False, error="HTTP 500")], expected, path=path)
    payload = record_lane_health([_res("flaky", fetched=3, saved=1)], expected, path=path)
    row = payload["lanes"]["flaky"]
    assert row["outcome"] == "ok"
    assert row["consecutive_failures"] == 0
    assert row["broken"] is False
    assert payload["summary"]["broken_lanes"] == []


def test_skipped_lane_carries_state_forward(tmp_path) -> None:
    """Configured lane with no result row (e.g. X cadence guard) keeps state."""
    path = tmp_path / "lane_health.json"
    expected = [_lane("cadence-lane", kind="social")]
    for _ in range(BROKEN_AFTER_CYCLES):
        record_lane_health(
            [_res("cadence-lane", success=False, error="HTTP 429")], expected, path=path
        )
    payload = record_lane_health([], expected, path=path)
    row = payload["lanes"]["cadence-lane"]
    assert row["outcome"] == "skipped"
    assert row["consecutive_failures"] == BROKEN_AFTER_CYCLES  # unchanged
    assert row["broken"] is True
    assert row["last_run_at"] is not None  # carried from the failing cycles


def test_removed_lane_row_dropped(tmp_path) -> None:
    """De-configuring a source removes its health row on the next cycle."""
    path = tmp_path / "lane_health.json"
    record_lane_health(
        [_res("keeper", fetched=1), _res("goner", fetched=1)],
        [_lane("keeper"), _lane("goner")],
        path=path,
    )
    payload = record_lane_health([_res("keeper", fetched=1)], [_lane("keeper")], path=path)
    assert set(payload["lanes"]) == {"keeper"}


def test_unexpected_result_rows_are_recorded(tmp_path) -> None:
    """Dynamic lanes (history sweeps, purchases) get rows without being expected."""
    path = tmp_path / "lane_health.json"
    payload = record_lane_health([_res("courtlistener-history", fetched=4, saved=4)], [], path=path)
    row = payload["lanes"]["courtlistener-history"]
    assert row["kind"] == "court"
    assert row["outcome"] == "ok"


def test_expected_lanes_track_feed_registry(monkeypatch) -> None:
    """Adding/removing/disabling a feed changes the enumerated lane set."""
    feeds = [
        FeedSource(id="alpha", name="Alpha", url="https://a.example/feed"),
        FeedSource(id="bravo", name="Bravo", url="https://b.example/feed", enabled=False),
    ]
    lanes = expected_lane_specs(
        feeds=feeds,
        include_feedly=False,
        include_courtlistener=False,
        include_web_keywords=False,
        include_datatheftnews=False,
        include_social=False,
        include_publications=False,
    )
    assert [lane.id for lane in lanes] == ["alpha"]

    feeds.append(FeedSource(id="charlie", name="Charlie", url="https://c.example/feed"))
    lanes = expected_lane_specs(
        feeds=feeds,
        include_feedly=False,
        include_courtlistener=False,
        include_web_keywords=False,
        include_datatheftnews=False,
        include_social=False,
        include_publications=False,
    )
    assert [lane.id for lane in lanes] == ["alpha", "charlie"]


def test_expected_lanes_full_enumeration(monkeypatch) -> None:
    """Non-RSS lanes come from their live registries, honoring config gates."""
    monkeypatch.setenv("WEB_KEYWORD_FEED_URLS", "https://alerts.example/rss/one")
    monkeypatch.setenv("SOCIAL_INGEST_ENABLED", "1")
    monkeypatch.setenv("REDDIT_SUBREDDITS", "netsec")
    monkeypatch.setenv("SOCIAL_SUBSCRIPTIONS_PATH", "/nonexistent/subs.json")
    lanes = {lane.id: lane.kind for lane in expected_lane_specs()}
    assert lanes.get("courtlistener-recap") == "court"
    assert lanes.get("datatheftnews") == "datatheftnews"
    assert lanes.get("web-keyword:alerts.example") == "web-keyword"
    assert lanes.get("pub-sei-common-sense-guide-7e") == "publications"
    assert lanes.get("social-reddit-netsec") == "social"
    # RSS registry flows through too.
    assert lanes.get("krebsonsecurity") == "rss"

    # Social parked (default): no social lanes even with subscriptions.
    monkeypatch.delenv("SOCIAL_INGEST_ENABLED")
    lanes = {lane.id for lane in expected_lane_specs()}
    assert "social-reddit-netsec" not in lanes


def test_summary_lines_call_out_broken_lanes(tmp_path) -> None:
    path = tmp_path / "lane_health.json"
    expected = [_lane("dead-a"), _lane("fine-b")]
    for _ in range(BROKEN_AFTER_CYCLES):
        payload = record_lane_health(
            [
                _res("dead-a", success=False, error="HTTP 404"),
                _res("fine-b", fetched=2, saved=1),
            ],
            expected,
            path=path,
        )
    lines = summarize_lane_health(payload)
    assert lines[0].startswith(HEALTH_MARKER)
    assert "ok=1" in lines[0]
    assert "broken=1" in lines[0]
    broken_lines = [line for line in lines if line.startswith(BROKEN_MARKER)]
    assert len(broken_lines) == 1
    assert "dead-a" in broken_lines[0]
    assert f"{BROKEN_AFTER_CYCLES} cycles" in broken_lines[0]
    assert "http_error" in broken_lines[0]
    assert "HTTP 404" in broken_lines[0]

    # All-healthy cycles emit no broken marker at all.
    payload = record_lane_health([_res("fine-b", fetched=2)], [_lane("fine-b")], path=path)
    assert not any(line.startswith(BROKEN_MARKER) for line in summarize_lane_health(payload))


def test_read_lane_health_missing_and_corrupt(tmp_path) -> None:
    missing = read_lane_health(tmp_path / "nope.json")
    assert missing["lanes"] == {}
    assert missing["summary"]["total"] == 0
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert read_lane_health(corrupt)["lanes"] == {}


def test_lanes_health_api_endpoint(tmp_path, monkeypatch) -> None:
    from apps.search import service
    from apps.search.api import app
    from shared.settings import Settings

    health_path = tmp_path / "lane_health.json"
    record_lane_health([_res("alpha", fetched=1, saved=1)], [_lane("alpha")], path=health_path)

    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(tmp_path / "processed.jsonl"),
        LANE_HEALTH_PATH=str(health_path),
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)

    with TestClient(app) as client:
        resp = client.get("/lanes/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["lanes"]["alpha"]["outcome"] == "ok"
        assert body["summary"] == {
            "total": 1,
            "healthy": 1,
            "broken": 0,
            "broken_lanes": [],
        }


def test_boot_snapshot_meta_embeds_lane_summary(tmp_path, monkeypatch) -> None:
    from scripts.export_boot_snapshot import build_snapshot

    monkeypatch.setenv("PROCESSED_ARTICLES_PATH", str(tmp_path / "processed.jsonl"))

    # No health file yet → meta omits the key (UI waits for live endpoint).
    monkeypatch.setenv("LANE_HEALTH_PATH", str(tmp_path / "missing.json"))
    _articles, meta, _tooling, _sources, _ledger = build_snapshot(limit=5)
    assert "lane_health" not in meta

    health_path = tmp_path / "lane_health.json"
    for _ in range(BROKEN_AFTER_CYCLES):
        record_lane_health(
            [
                _res("dead-a", success=False, error="HTTP 404"),
                _res("fine-b", fetched=2, saved=1),
            ],
            [_lane("dead-a"), _lane("fine-b")],
            path=health_path,
        )
    monkeypatch.setenv("LANE_HEALTH_PATH", str(health_path))
    _articles, meta, _tooling, _sources, _ledger = build_snapshot(limit=5)
    summary = meta["lane_health"]
    assert summary["broken"] == 1
    assert summary["broken_lanes"] == ["dead-a"]
    assert summary["healthy"] == 1
    assert summary["generated_at"] == json.loads(health_path.read_text())["generated_at"]
