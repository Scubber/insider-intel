"""User-picked social sources (subreddits / X follows) — single-tenant JSON store.

Deliberately a config object, not a user-profile system: a future
multi-user layer would namespace these records by owner without changing
their shape.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_SUBSCRIPTIONS_PATH = "data/config/social_subscriptions.json"

Platform = Literal["reddit", "x"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_handle(platform: str, handle: str) -> str:
    """Canonical id: lowercase, no r/ or @ prefixes."""
    cleaned = (handle or "").strip().lower()
    if platform == "reddit":
        cleaned = cleaned.removeprefix("/r/").removeprefix("r/").strip("/")
    else:
        cleaned = cleaned.lstrip("@")
    return cleaned


def social_source_id(platform: str, handle: str) -> str:
    return f"social-{platform}-{normalize_handle(platform, handle)}"


class SocialSubscription(BaseModel):
    platform: Platform
    id: str = Field(..., description="Normalized subreddit name or X handle")
    name: str = Field(default="", description="Display name (r/... or @...)")
    enabled: bool = True
    origin: Literal["catalog", "manual"] = "manual"
    use_cases: list[str] = Field(default_factory=list)
    added_at: datetime = Field(default_factory=_utc_now)

    def display_name(self) -> str:
        if self.name:
            return self.name
        return f"r/{self.id}" if self.platform == "reddit" else f"@{self.id}"

    def source_id(self) -> str:
        return social_source_id(self.platform, self.id)


class SocialSubscriptionStore:
    """JSON-file CRUD keyed by (platform, id); atomic writes."""

    def __init__(self, path: str | Path = DEFAULT_SUBSCRIPTIONS_PATH) -> None:
        self._path = Path(path)

    def list(self) -> list[SocialSubscription]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("Could not read %s; treating as empty", self._path)
            return []
        rows = payload.get("subscriptions", []) if isinstance(payload, dict) else payload
        out: list[SocialSubscription] = []
        for row in rows:
            try:
                out.append(SocialSubscription.model_validate(row))
            except ValueError:
                logger.warning("Skipping malformed subscription row: %r", row)
        return out

    def add(
        self,
        platform: str,
        handle: str,
        *,
        name: str | None = None,
        origin: str = "manual",
        use_cases: list[str] | None = None,
    ) -> SocialSubscription:
        """Add (idempotent): re-adding an existing source re-enables it."""
        normalized = normalize_handle(platform, handle)
        if not normalized:
            raise ValueError("empty social handle")
        entry = SocialSubscription(
            platform=platform,  # type: ignore[arg-type]
            id=normalized,
            name=name or "",
            origin=origin,  # type: ignore[arg-type]
            use_cases=list(use_cases or []),
        )
        current = self.list()
        for existing in current:
            if existing.platform == entry.platform and existing.id == entry.id:
                existing.enabled = True
                if name:
                    existing.name = name
                if use_cases:
                    existing.use_cases = list(use_cases)
                self._write(current)
                return existing
        current.append(entry)
        self._write(current)
        return entry

    def remove(self, platform: str, handle: str) -> bool:
        normalized = normalize_handle(platform, handle)
        current = self.list()
        kept = [
            s for s in current if not (s.platform == platform and s.id == normalized)
        ]
        if len(kept) == len(current):
            return False
        self._write(kept)
        return True

    def enabled(self, platform: str) -> list[SocialSubscription]:
        return [s for s in self.list() if s.platform == platform and s.enabled]

    def _write(self, subscriptions: list[SocialSubscription]) -> None:
        payload: dict[str, Any] = {
            "subscriptions": [
                s.model_dump(mode="json") for s in sorted(
                    subscriptions, key=lambda s: (s.platform, s.id)
                )
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self._path)
