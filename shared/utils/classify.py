"""Heuristic use-case + insider-type classification.

Runs for every processed article (all channels). Matching conventions
mirror shared.utils.entities: multi-word / long phrases match as
substrings, short tokens with word boundaries.
"""

from __future__ import annotations

import re

from shared.schemas.articles import ExtractedEntities, InsiderType
from shared.taxonomy.use_cases import USE_CASES

# Cue phrases per insider type. Precedence: malicious > negligent >
# unintentional — deliberate harm outranks sloppiness outranks accident
# when a text carries mixed signals.
_MALICIOUS_CUES: tuple[str, ...] = (
    "revenge",
    "retaliation",
    "disgruntled",
    "sabotage",
    "sold data",
    "sold credentials",
    "selling company",
    "sell the data",
    "stole",
    "steal",
    "theft of trade secrets",
    "espionage",
    "fraud",
    "deliberately",
    "intentionally leaked",
    "planted",
    "extortion",
    "blackmail",
)

_NEGLIGENT_CUES: tuple[str, ...] = (
    "negligent",
    "negligence",
    "recklessness",
    "reckless",
    "careless",
    "against policy",
    "ignored policy",
    "ignored the policy",
    "violates policy",
    "policy violation",
    "knew the rules",
    "didn't bother",
    "did not bother",
    "skipped the process",
    "everyone does it",
    "shortcut",
    "didn't disclose",
    "did not disclose",
    "without telling",
    "without approval",
    "never told",
    "hiding it from",
    "keep it secret",
)

_UNINTENTIONAL_CUES: tuple[str, ...] = (
    "accidentally",
    "accidental",
    "by accident",
    "mistakenly",
    "by mistake",
    "honest mistake",
    "misconfigured",
    "misconfiguration",
    "fat-finger",
    "fat finger",
    "wrong recipient",
    "sent to the wrong",
    "phished",
    "fell for",
    "unknowingly",
    "didn't realize",
    "did not realize",
    "unaware",
)

# Insider types keyed by ITM Motive technique corroboration.
_MALICIOUS_ITM_IDS = frozenset({"MT005", "MT012", "MT017"})
_NEGLIGENT_ITM_IDS = frozenset({"MT015", "MT022"})


def _phrase_matches(phrase: str, lowered: str) -> bool:
    """Substring for multi-word / long phrases; word-boundary for short tokens."""
    if " " in phrase or len(phrase) >= 8:
        return phrase in lowered
    return (
        re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", lowered)
        is not None
    )


def _itm_hit_ids(entities: ExtractedEntities | None) -> set[str]:
    if entities is None:
        return set()
    # Subsections (IF038.1) count toward their parent technique (IF038).
    return {hit.id.split(".", 1)[0].upper() for hit in entities.itm_hits}


def classify_use_cases(
    text: str,
    entities: ExtractedEntities | None = None,
) -> list[str]:
    """Return matched use-case ids, in registry order.

    A use case matches when one of its ITM techniques was hit, or when its
    keyword phrases match. Weak (generic) keywords need a second distinct
    keyword hit unless an ITM technique corroborates.
    """
    lowered = (text or "").lower()
    if not lowered.strip():
        return []
    hit_ids = _itm_hit_ids(entities)

    matched: list[str] = []
    for uc in USE_CASES:
        if any(itm_id in hit_ids for itm_id in uc.itm_ids):
            matched.append(uc.id)
            continue
        strong = [
            kw
            for kw in uc.keywords
            if kw not in uc.weak_keywords and _phrase_matches(kw, lowered)
        ]
        weak = [kw for kw in uc.weak_keywords if _phrase_matches(kw, lowered)]
        if strong or len(weak) >= 2:
            matched.append(uc.id)
    return matched


def classify_insider_type(
    text: str,
    entities: ExtractedEntities | None = None,
) -> InsiderType | None:
    """Infer the insider disposition, or None when no cues fire."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    hit_ids = _itm_hit_ids(entities)

    if hit_ids & _MALICIOUS_ITM_IDS or any(
        _phrase_matches(cue, lowered) for cue in _MALICIOUS_CUES
    ):
        return "malicious"
    if hit_ids & _NEGLIGENT_ITM_IDS or any(
        _phrase_matches(cue, lowered) for cue in _NEGLIGENT_CUES
    ):
        return "negligent"
    if any(_phrase_matches(cue, lowered) for cue in _UNINTENTIONAL_CUES):
        return "unintentional"
    return None
