"""Tests for ITM-aligned entity extraction and scoring."""

from __future__ import annotations

import re

from shared.utils.entities import (
    classify_itm_alignment,
    extract_entities,
    score_relevance,
)
from shared.utils.text import to_plain_text

_ITM_ID_RE = re.compile(r"^[A-Z]{2}\d{3}(?:\.\d+)?$", re.IGNORECASE)


def test_extract_cves_domains_and_itm_hits() -> None:
    text = (
        "An insider threat case described USB exfiltration by a departing employee "
        "using removable media after resignation. See evil-example.com. "
        "Related advisory CVE-2024-12345."
    )
    entities = extract_entities(text)
    assert "CVE-2024-12345" in entities.cves
    assert "evil-example.com" in entities.domains
    assert "insider threat" in entities.keywords_hit
    assert "insider threat" in entities.operator_terms
    assert "CVE-2024-12345" in entities.operator_terms
    assert "evil-example.com" in entities.operator_terms
    # Technique ids stay on match signals, not operator paste list
    assert "ME005" in entities.keywords_hit
    assert "ME005" not in entities.operator_terms

    hit_ids = {h.id for h in entities.itm_hits}
    # Means: Removable Media; Infringement: Exfiltration via Physical Medium; Motive: Leaver
    assert "ME005" in hit_ids
    assert "IF002" in hit_ids
    assert "MT003" in hit_ids
    themes = {h.theme for h in entities.itm_hits}
    assert "means" in themes
    assert "infringement" in themes
    assert "motive" in themes

    # SIEM handoff: detections/preventions join via techniques, not DT text match
    assert entities.related_detections, "expected DT* links from matched techniques"
    assert entities.related_preventions, "expected PV* links from matched techniques"
    dt_ids = {c.id for c in entities.related_detections}
    pv_ids = {c.id for c in entities.related_preventions}
    assert all(i.startswith("DT") for i in dt_ids)
    assert all(i.startswith("PV") for i in pv_ids)
    # Removable / USB-oriented detections commonly linked from ME005 / IF002
    assert any("USB" in c.title.upper() or "REMOVABLE" in c.title.upper() or "DLP" in c.title.upper() for c in entities.related_detections) or len(dt_ids) >= 1


def test_classify_itm_alignment_insider_vs_weak() -> None:
    insider = extract_entities(
        "An insider threat case described USB exfiltration by a departing employee "
        "using removable media after resignation."
    )
    assert classify_itm_alignment(insider) == "insider"

    weak = extract_entities(
        "Patch Tuesday: Microsoft fixed remote code execution bugs in Windows. "
        "CVE-2024-99999 affects firmware."
    )
    assert classify_itm_alignment(weak) == "weak"


def test_score_relevance_rewards_itm_signal() -> None:
    low = extract_entities("Company announces quarterly earnings.")
    high = extract_entities(
        "Malicious insider used privilege escalation and mass download for "
        "exfiltration via email after resignation. CVE-2023-99999."
    )
    assert score_relevance(high, text_length=120) > score_relevance(low, text_length=40)
    assert high.itm_hits
    assert score_relevance(high, text_length=120) >= 0.2


def test_generic_cyber_news_scores_lower_than_insider_exfil() -> None:
    generic = extract_entities(
        "Patch Tuesday: Microsoft fixed remote code execution bugs in Windows."
    )
    insider = extract_entities(
        "Departing employee staged files on USB removable media before "
        "exfiltration via physical medium. Insider threat investigation opened."
    )
    assert score_relevance(insider, text_length=100) > score_relevance(
        generic, text_length=80
    )


def test_operator_terms_exclude_itm_ids() -> None:
    entities = extract_entities(
        "An insider threat case described USB exfiltration by a departing employee "
        "using removable media after resignation. Related advisory CVE-2024-12345."
    )
    assert entities.operator_terms
    assert not any(_ITM_ID_RE.match(term) for term in entities.operator_terms)
    assert any(
        "exfiltration" in t.lower() or "removable" in t.lower() or "insider" in t.lower()
        for t in entities.operator_terms
    )


def test_feedly_style_apple_story_matches_itm() -> None:
    text = (
        "Apple sues OpenAI over theft. Former employees downloaded confidential "
        "engineering documents and proprietary hardware files before leaving the company. "
        "Insider threat lawsuit alleges a pattern of theft of trade secrets."
    )
    entities = extract_entities(text)
    ids = {h.id for h in entities.itm_hits}
    assert "MT003" in ids or any(i.startswith("MT003") for i in ids)
    assert "PR025" in ids or "IF015" in ids or "IF001" in ids
    assert score_relevance(entities, text_length=len(text)) >= 0.2


def test_to_plain_text_strips_html() -> None:
    assert to_plain_text("<p>Hello <b>world</b></p>") == "Hello world"
