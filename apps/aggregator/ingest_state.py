"""Tiny persisted key→value state for incremental ingestion watermarks."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = "data/state/ingest_state.json"


class JsonIngestState:
    """Flat JSON dict on disk (e.g. {"courtlistener:opinions": "2026-07-10"}).

    Writes are atomic (tmp + replace); a corrupt or missing file resets to {}.
    """

    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self._state: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable ingest state %s: %s", self.path, exc)
            return {}
        if not isinstance(payload, dict):
            logger.warning("Ignoring malformed ingest state %s (not a dict)", self.path)
            return {}
        return {str(k): str(v) for k, v in payload.items()}

    def get(self, key: str) -> str | None:
        return self._state.get(key)

    def set(self, key: str, value: str) -> None:
        self._state[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
