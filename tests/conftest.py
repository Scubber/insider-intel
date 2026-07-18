"""Shared test configuration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_courtlistener_politeness_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness sleeps between CourtListener calls would only slow tests."""
    monkeypatch.setenv("COURTLISTENER_REQUEST_DELAY_SECONDS", "0")
