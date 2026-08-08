"""Synthesized technique hunt patterns — the corpus-level hunt product.

One LLM call per observed technique (on the corpus-refresh job, never the API)
generalizes that technique's case material into environment-portable hunt
patterns. Stored as a materialized view at ``data/state/technique_hunts.json``
(job-written under ``state/``, API reads it — same contract as
``technique_seeds.json``), keyed by an input signature so a technique is only
re-synthesized when its case material changes.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CHANNELS = ("email", "chat", "network", "endpoint", "cloud", "identity", "physical", "human")
Channel = Literal["email", "chat", "network", "endpoint", "cloud", "identity", "physical", "human"]

MAX_PATTERNS_PER_TECHNIQUE = 4


class HuntPattern(BaseModel):
    """One generalized, environment-portable hunt for a technique."""

    name: str
    who_class: str = ""
    action: str = ""
    target_class: str = ""
    channel: Channel | None = None
    logic: str
    log_sources: list[str] = Field(default_factory=list)
    thresholds: str = ""
    false_positives: str = ""


class TechniqueHuntEntry(BaseModel):
    """Synthesized patterns for one technique, with regeneration bookkeeping."""

    technique_id: str
    signature: str = Field(description="Hash of the input case material at generation time")
    generated_at: str = ""
    model: str = ""
    case_count: int = 0
    adjudicated_count: int = 0
    patterns: list[HuntPattern] = Field(default_factory=list)


def _clean(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def parse_patterns(raw: object) -> list[HuntPattern]:
    """Coerce a raw LLM reply's ``patterns`` list; bad entries drop, never raise."""
    if not isinstance(raw, dict):
        return []
    out: list[HuntPattern] = []
    for item in raw.get("patterns") or []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 80)
        logic = _clean(item.get("logic"), 500)
        if not name or not logic:
            continue
        channel = str(item.get("channel") or "").strip().lower()
        sources = [
            s for s in (_clean(x, 80) for x in (item.get("log_sources") or [])[:6]) if s
        ]
        out.append(
            HuntPattern(
                name=name,
                who_class=_clean(item.get("who_class"), 80),
                action=_clean(item.get("action"), 240),
                target_class=_clean(item.get("target_class"), 80),
                channel=channel if channel in CHANNELS else None,
                logic=logic,
                log_sources=sources,
                thresholds=_clean(item.get("thresholds"), 300),
                false_positives=_clean(item.get("false_positives"), 300),
            )
        )
        if len(out) >= MAX_PATTERNS_PER_TECHNIQUE:
            break
    return out
