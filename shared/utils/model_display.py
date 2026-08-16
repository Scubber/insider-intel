"""Human display names for enricher model ids (provenance labels).

``PerCaseForensics.model`` stores the exact served-model id stamped at
enrichment time (``claude-haiku-4-5-20251001``, ``Qwen/Qwen3.8-27B-FP8``, …).
The public UI shows an "Enriched by <label>" provenance line built from it;
this module is the single source of truth for that label. It runs at hit
projection time (``apps/search/index.py::_to_hit``), so both client data
paths — the live API and the static boot snapshot exported from the same
hits — ship one precomputed ``enriched_by`` string and the client renders it
verbatim.

Unknown ids degrade to a readable fallback (org prefix and date suffix
stripped) rather than raw plumbing strings; a missing/empty id returns None
so callers omit the line entirely — the UI must never print "None".
"""

from __future__ import annotations

import re

# Exact ids seen in the corpus (keys lowercased). Extend as new backends land.
_KNOWN_MODEL_LABELS = {
    "qwen/qwen3.8-27b-fp8": "Qwen 3.8 27B (local)",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "grok-4": "Grok 4",
    "gpt-4o": "GPT-4o",
}

# Trailing Anthropic-style release date, e.g. claude-haiku-4-5-20251001.
_DATE_SUFFIX_RE = re.compile(r"-20\d{6}$")

# claude-<chassis>-<major>[-<minor>] (after the date suffix is stripped).
_CLAUDE_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d+))?$")


def enricher_display_name(model_id: str | None) -> str | None:
    """Map a served-model id to its human provenance label.

    Known ids get their curated label; unknown ids fall back to a cleaned-up
    rendering (org prefix + date suffix stripped, hyphens spaced, lowercase
    words capitalized). None/empty/non-string input returns None — the caller
    omits the provenance line rather than ever showing "None".
    """
    if not isinstance(model_id, str):
        return None
    raw = model_id.strip()
    if not raw:
        return None
    known = _KNOWN_MODEL_LABELS.get(raw.lower())
    if known:
        return known

    # Fallback: strip the org prefix (Qwen/…, meta-llama/…) and date suffix.
    name = _DATE_SUFFIX_RE.sub("", raw.rsplit("/", 1)[-1].strip())
    if not name:
        return None
    claude = _CLAUDE_RE.match(name.lower())
    if claude:
        chassis, major, minor = claude.groups()
        version = f"{major}.{minor}" if minor else major
        return f"Claude {chassis.capitalize()} {version}"
    # Generic prettify: hyphens/underscores to spaces; capitalize plain
    # lowercase words, keep mixed-case/uppercase tokens (32B, FP8) as-is.
    tokens = [t for t in re.split(r"[-_]+", name) if t]
    return " ".join(t.capitalize() if t.isalpha() and t.islower() else t for t in tokens)
