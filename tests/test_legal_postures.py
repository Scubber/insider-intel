"""Cross-site legal-posture contract (the drift tripwire).

``legal_posture`` is enforced in three deliberately-unlinked places:
the schema enum (shared/schemas/forensics.py::LEGAL_POSTURES), the enricher
prompt (shared/llm/base.py::ENRICH_SYSTEM_PROMPT), and the case-strength
weights (shared/utils/evidence.py::POSTURE_WEIGHT — pure stdlib, no schema
import). A posture present in one site but missing from POSTURE_WEIGHT is
silently UNCAPPED in case_strength — an FIR/bail document whose methods the
LLM stamped "adjudicated" would count court-proven. These tests fail on any
drift between the three sites.
"""

from __future__ import annotations

from shared.llm.base import ENRICH_SYSTEM_PROMPT
from shared.schemas.forensics import LEGAL_POSTURES, parse_forensics_json
from shared.utils.evidence import (
    POSTURE_ADJUDICATED_MIN_WEIGHT,
    POSTURE_WEIGHT,
    case_strength,
)

# "none"/"unknown" deliberately carry no weight (uncapped by design so legacy
# rows don't degrade on absent data); everything else MUST be weighted.
_UNWEIGHTED_BY_DESIGN = {"none", "unknown"}

INDIAN_PRE_ADJUDICATIVE = (
    "fir_allegation",
    "charge_sheet",
    "disciplinary_proceeding",
    "interim_injunction",
    "bail",
    "quashing",
    "writ_review",
    "arbitral_proceeding",
    "acquittal",  # adjudicated AGAINST proof of the conduct — capped on purpose
)


def test_every_enum_posture_is_weighted_or_exempt() -> None:
    for posture in LEGAL_POSTURES:
        if posture in _UNWEIGHTED_BY_DESIGN:
            continue
        assert posture in POSTURE_WEIGHT, (
            f"legal_posture {posture!r} is in LEGAL_POSTURES but missing from "
            "POSTURE_WEIGHT — its 'adjudicated' claims would be UNCAPPED in "
            "case_strength. Add a weight (pre-adjudicative stages < "
            f"{POSTURE_ADJUDICATED_MIN_WEIGHT})."
        )


def test_every_enum_posture_is_taught_to_the_llm() -> None:
    for posture in LEGAL_POSTURES:
        assert posture in ENRICH_SYSTEM_PROMPT, (
            f"legal_posture {posture!r} is in LEGAL_POSTURES but absent from "
            "ENRICH_SYSTEM_PROMPT — the model cannot emit a value the prompt "
            "doesn't teach (parse coerces it to 'unknown')."
        )


def test_no_orphan_weights() -> None:
    for posture in POSTURE_WEIGHT:
        assert posture in LEGAL_POSTURES, (
            f"POSTURE_WEIGHT has {posture!r} which LEGAL_POSTURES doesn't "
            "define — parse coercion would turn it into 'unknown' before the "
            "weight could ever apply."
        )


def test_indian_pre_adjudicative_stages_sit_below_the_floor() -> None:
    for posture in INDIAN_PRE_ADJUDICATIVE:
        assert POSTURE_WEIGHT[posture] < POSTURE_ADJUDICATED_MIN_WEIGHT, posture
    # Genuine merits decisions rank adjudicated.
    assert POSTURE_WEIGHT["trial_judgment"] >= POSTURE_ADJUDICATED_MIN_WEIGHT
    assert POSTURE_WEIGHT["civil_decree"] >= POSTURE_ADJUDICATED_MIN_WEIGHT


def test_bail_order_with_rich_detail_stays_alleged() -> None:
    """The Abhinav Gupta shape: forensic detail inside a bail order.

    Indian judgments are often richest at pre-adjudication stages; the cap is
    what makes that richness safe to count.
    """
    methods = [{"claim_status": "adjudicated"}]
    assert case_strength(methods, "bail") == "alleged"
    assert case_strength(methods, "quashing") == "alleged"
    assert case_strength(methods, "fir_allegation") == "alleged"
    assert case_strength(methods, "disciplinary_proceeding") == "alleged"
    # A real merits decision keeps its strength…
    assert case_strength(methods, "trial_judgment") == "adjudicated/admitted"
    # …and posture never promotes an alleged-only record.
    assert case_strength([{"claim_status": "alleged"}], "trial_judgment") == "alleged"


def test_parse_accepts_indian_postures_and_coerces_junk() -> None:
    ok = parse_forensics_json(
        {"is_insider_case": True, "legal_posture": "bail"}, link="l", title="t"
    )
    assert ok.legal_posture == "bail"
    junk = parse_forensics_json(
        {"is_insider_case": True, "legal_posture": "vibes"}, link="l", title="t"
    )
    assert junk.legal_posture == "unknown"
