"""Heuristic entity extraction aligned to the Insider Threat Matrix™."""

from __future__ import annotations

import re
from typing import Literal

from shared.itm.aliases import INSIDER_FRAMING_KEYWORDS
from shared.itm.controls import resolve_controls
from shared.itm.index import ItmTechnique, load_itm_index
from shared.schemas.articles import ExtractedEntities, ItmHit

# CVE identifiers, e.g. CVE-2024-12345
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

# Simple domain matcher (excludes common file extensions mistaken as TLDs)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|gov|edu|ru|cn|uk|de|info|biz)\b",
    re.IGNORECASE,
)

_SKIP_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "localhost",
        "w3.org",
        "schema.org",
    }
)

# Theme weights for relevance (infringement / prep / AF > means > motive)
_THEME_WEIGHT: dict[str, float] = {
    "infringement": 0.14,
    "preparation": 0.11,
    "anti-forensics": 0.11,
    "means": 0.08,
    "motive": 0.05,
}

_ITM_URL_THEME = {
    "motive": "AR1",
    "means": "AR2",
    "preparation": "AR3",
    "infringement": "AR4",
    "anti-forensics": "AR5",
}

# Technique ids like ME005 / IF002.1 — taxonomy, not SIEM/chat paste terms
_ITM_ID_RE = re.compile(r"^[A-Z]{2}\d{3}(?:\.\d+)?$", re.IGNORECASE)


def itm_public_url(hit: ItmHit | ItmTechnique) -> str:
    """Canonical public matrix URL for a technique / section."""
    article_id = getattr(hit, "article_id", None) or _ITM_URL_THEME.get(
        getattr(hit, "theme", ""), "AR4"
    )
    tech_id = hit.id
    # Subsections share the parent section path on the public site.
    section_id = tech_id.split(".", 1)[0]
    return f"https://insiderthreatmatrix.org/articles/{article_id}/sections/{section_id}"


def extract_cves(text: str) -> list[str]:
    found = {m.group(0).upper() for m in _CVE_RE.finditer(text)}
    return sorted(found)


def extract_domains(text: str) -> list[str]:
    found: set[str] = set()
    for match in _DOMAIN_RE.finditer(text):
        domain = match.group(0).lower().rstrip(".")
        if domain not in _SKIP_DOMAINS:
            found.add(domain)
    return sorted(found)


def find_framing_keywords(text: str) -> list[str]:
    lowered = text.lower()
    return [kw for kw in INSIDER_FRAMING_KEYWORDS if kw in lowered]


def _alias_matches(alias: str, lowered: str) -> bool:
    """Substring for multi-word / long phrases; word-boundary for short tokens."""
    if " " in alias or len(alias) >= 8:
        return alias in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered) is not None


def match_itm_techniques(text: str) -> list[ItmHit]:
    """Match article text against ITM technique titles and aliases."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return []

    hits: list[ItmHit] = []
    seen: set[str] = set()
    techniques = load_itm_index().techniques

    # Longer aliases first so specific phrases win over short title tokens.
    candidates: list[tuple[str, ItmTechnique]] = []
    for tech in techniques:
        for alias in tech.aliases:
            alias_l = alias.strip().lower()
            if len(alias_l) < 3:
                continue
            # Skip ultra-generic single tokens that flood false positives
            if " " not in alias_l and alias_l in {
                "access",
                "theft",
                "printing",
                "placement",
                "stalling",
                "virtualization",
                "tripwires",
                "espionage",
                "sabotage",
                "bribe",
                "snooping",
            }:
                continue
            candidates.append((alias_l, tech))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    matched_aliases: dict[str, list[str]] = {}
    for alias, tech in candidates:
        if tech.id in seen:
            continue
        if _alias_matches(alias, lowered):
            seen.add(tech.id)
            matched_aliases.setdefault(tech.id, []).append(alias)
            hits.append(
                ItmHit(
                    id=tech.id,
                    title=tech.title,
                    theme=tech.theme,
                    article_id=tech.article_id,
                    matched_aliases=matched_aliases[tech.id],
                )
            )

    hits.sort(key=lambda h: (h.theme, h.id))
    return hits


def _is_operator_term(term: str) -> bool:
    """True if term is useful for Teams/email/SIEM paste (not bare ITM taxonomy)."""
    cleaned = (term or "").strip()
    if len(cleaned) < 3:
        return False
    if _ITM_ID_RE.match(cleaned):
        return False
    # Very long taxonomy titles are poor chat/email search strings
    if len(cleaned) > 80:
        return False
    return True


def mint_operator_terms(
    *,
    framing: list[str],
    itm_hits: list[ItmHit],
    cves: list[str],
    domains: list[str],
) -> list[str]:
    """Build searchable operator terms distinct from taxonomy match signals."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = value.strip()
        if not _is_operator_term(cleaned):
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(cleaned)

    for kw in framing:
        add(kw)
    for hit in itm_hits:
        for alias in hit.matched_aliases:
            add(alias)
    for cve in cves:
        add(cve)
    for domain in domains:
        add(domain)
    return terms


def extract_entities(text: str) -> ExtractedEntities:
    """Extract CVEs, domains, ITM hits, hunt signals, and operator terms."""
    itm_hits = match_itm_techniques(text)
    framing = find_framing_keywords(text)
    cves = extract_cves(text)
    domains = extract_domains(text)
    related_detections, related_preventions = resolve_controls(itm_hits)

    keywords: list[str] = []
    seen_kw: set[str] = set()
    for kw in framing:
        if kw not in seen_kw:
            keywords.append(kw)
            seen_kw.add(kw)
    for hit in itm_hits:
        for alias in hit.matched_aliases:
            if alias not in seen_kw:
                keywords.append(alias)
                seen_kw.add(alias)
        # Always include technique id for filter / match signals
        if hit.id not in seen_kw:
            keywords.append(hit.id)
            seen_kw.add(hit.id)

    return ExtractedEntities(
        cves=cves,
        domains=domains,
        keywords_hit=keywords,
        operator_terms=mint_operator_terms(
            framing=framing,
            itm_hits=itm_hits,
            cves=cves,
            domains=domains,
        ),
        itm_hits=itm_hits,
        related_detections=related_detections,
        related_preventions=related_preventions,
    )


def classify_itm_alignment(entities: ExtractedEntities) -> Literal["insider", "weak"]:
    """Classify whether the article is an ITM-aligned insider scenario.

    ``insider`` requires at least one ITM technique hit plus either Insider
    Threat Matrix–style framing language or a Motive-theme technique hit.
    """
    if not entities.itm_hits:
        return "weak"
    framing_hits = [
        kw for kw in entities.keywords_hit if kw in INSIDER_FRAMING_KEYWORDS
    ]
    has_motive = any(hit.theme.lower() == "motive" for hit in entities.itm_hits)
    if framing_hits or has_motive:
        return "insider"
    return "weak"


def score_relevance(entities: ExtractedEntities, *, text_length: int = 0) -> float:
    """Heuristic insider-relevance score in [0, 1], weighted by ITM theme."""
    score = 0.0
    alignment = classify_itm_alignment(entities)
    # CVEs alone must not inflate general vuln news into ITM scenarios
    if alignment == "insider":
        score += min(len(entities.cves) * 0.15, 0.3)
    else:
        score += min(len(entities.cves) * 0.03, 0.06)

    theme_scores: dict[str, float] = {}
    for hit in entities.itm_hits:
        weight = _THEME_WEIGHT.get(hit.theme, 0.06)
        theme_scores[hit.theme] = theme_scores.get(hit.theme, 0.0) + weight
    # Cap per theme so one theme with many subsection hits does not dominate
    for theme, value in theme_scores.items():
        cap = 0.35 if theme in {"infringement", "preparation", "anti-forensics"} else 0.25
        score += min(value, cap)

    framing_hits = [
        kw for kw in entities.keywords_hit if kw in INSIDER_FRAMING_KEYWORDS
    ]
    if framing_hits:
        score += min(0.08 + 0.04 * len(framing_hits), 0.2)

    score += min(len(entities.domains) * 0.02, 0.06)
    if text_length > 80:
        score += 0.03
    return round(min(score, 1.0), 4)
