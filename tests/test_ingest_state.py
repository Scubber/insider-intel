"""Tests for the persisted ingestion watermark state."""

from __future__ import annotations

from pathlib import Path

from apps.aggregator.ingest_state import JsonIngestState


def test_ingest_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "ingest_state.json"
    state = JsonIngestState(path)
    assert state.get("courtlistener:dockets") is None

    state.set("courtlistener:dockets", "2026-07-10")
    assert state.get("courtlistener:dockets") == "2026-07-10"

    reopened = JsonIngestState(path)
    assert reopened.get("courtlistener:dockets") == "2026-07-10"


def test_ingest_state_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "ingest_state.json"
    path.write_text("{not json", encoding="utf-8")
    state = JsonIngestState(path)
    assert state.get("anything") is None
    state.set("k", "v")
    assert JsonIngestState(path).get("k") == "v"
