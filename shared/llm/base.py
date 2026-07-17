"""Classifier LLM provider contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from shared.schemas.articles import InsiderType
from shared.taxonomy.use_cases import use_case_ids

# Truncation cap for prompts (mirrors apps/search/ttp_extract.py MAX_TEXT_CHARS)
MAX_TEXT_CHARS = 3500


class ClassificationResult(BaseModel):
    use_cases: list[str] = Field(default_factory=list)
    insider_type: InsiderType | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str | None = None

    def sanitized(self) -> ClassificationResult:
        """Drop use-case ids the model invented outside the registry."""
        valid = set(use_case_ids())
        return self.model_copy(
            update={"use_cases": [uc for uc in self.use_cases if uc in valid]}
        )


class ClassifierProvider(Protocol):
    def classify(self, *, title: str, text: str) -> ClassificationResult | None: ...


CLASSIFY_SYSTEM_PROMPT = """\
You classify short posts/articles for an insider-threat intel tool.
Reply with ONLY a JSON object, no prose, matching:
{"use_cases": [...], "insider_type": ..., "confidence": 0.0-1.0, "rationale": "..."}

use_cases — choose all that apply, [] if none:
- overemployment: secretly working 2+ jobs, J2/OE, undisclosed moonlighting
- data-exfiltration: taking/leaking company data, files, trade secrets
- credential-misuse: sharing/abusing logins, badges, privileged access
- shadow-it: unsanctioned apps/devices/AI tools used for work

insider_type — exactly one, or null when none fits:
- malicious: intentional harm or personal gain (theft, sabotage, fraud, espionage)
- negligent: knew the rules and disregarded them (policy shortcuts, recklessness)
- unintentional: honest mistake or victim (accident, misconfiguration, phished)
"""


def build_user_prompt(title: str, text: str) -> str:
    return f"TITLE: {title}\n\nTEXT: {(text or '')[:MAX_TEXT_CHARS]}"
