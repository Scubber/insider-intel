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

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAX_PATTERNS_PER_TECHNIQUE = 4


class HuntPattern(BaseModel):
    """One generalized, tool-agnostic hunt pattern for a technique.

    ``detect``/``prevent`` are plain-language methods — technical, people, or
    process (training, offboarding, HR partnership) — never query syntax or
    product names.
    """

    name: str
    who_class: str = ""
    behavior: str = ""
    detect: list[str] = Field(default_factory=list)
    prevent: list[str] = Field(default_factory=list)
    noise: str = ""


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
        detect = [d for d in (_clean(x, 300) for x in (item.get("detect") or [])[:4]) if d]
        if not name or not detect:
            continue
        prevent = [p for p in (_clean(x, 300) for x in (item.get("prevent") or [])[:4]) if p]
        out.append(
            HuntPattern(
                name=name,
                who_class=_clean(item.get("who_class"), 80),
                behavior=_clean(item.get("behavior"), 300),
                detect=detect,
                prevent=prevent,
                noise=_clean(item.get("noise"), 300),
            )
        )
        if len(out) >= MAX_PATTERNS_PER_TECHNIQUE:
            break
    return out
