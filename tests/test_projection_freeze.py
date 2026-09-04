"""Projection freeze + verbatim grounding stamps (audit items D4/D5).

The selection projection must never let richness alone flip is_insider_case
(a verbose low-confidence generation re-adjudicating a case), and every
claimed evidence quote must carry a deterministic grounding verdict.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from shared.schemas.forensics import (
    CaseMethod,
    EnrichmentRecord,
    PerCaseForensics,
    _enrichment_signature,
    enrichment_richness,
    project_additive_fields,
    project_from_history,
    select_best_enrichment,
    stamp_quote_verbatim,
)


def _gen(
    *,
    insider: bool,
    confidence: float,
    methods: int,
    note: str | None = "note",
    when: int = 1,
    schema: int | None = None,
) -> EnrichmentRecord:
    forensics = PerCaseForensics(
        link="https://example.com/a",
        title="t",
        is_insider_case=insider,
        confidence=confidence,
        methods=[CaseMethod(action=f"a{i}") for i in range(methods)],
        extracted_at=datetime(2026, 1, when, tzinfo=UTC),
    )
    if schema is not None:
        forensics = forensics.model_copy(update={"schema_version": schema})
    return EnrichmentRecord(ai_summary=note, forensics=forensics)


def test_richness_cannot_flip_a_confident_verdict() -> None:
    # The sweep hazard: a chatty 7-method generation at conf 0.2 must not
    # unseat a confident insider adjudication with 3 methods.
    confident_insider = _gen(insider=True, confidence=0.9, methods=3, when=1)
    verbose_noncase = _gen(insider=False, confidence=0.2, methods=7, when=2)
    picked = select_best_enrichment([confident_insider, verbose_noncase])
    assert picked is confident_insider


def test_richness_still_selects_within_a_verdict() -> None:
    thin = _gen(insider=True, confidence=0.9, methods=1, when=1)
    rich = _gen(insider=True, confidence=0.7, methods=6, when=2)
    assert select_best_enrichment([thin, rich]) is rich


def test_confidence_tie_breaks_to_richness() -> None:
    a = _gen(insider=True, confidence=0.5, methods=2, when=1)
    b = _gen(insider=False, confidence=0.5, methods=5, when=2)
    assert select_best_enrichment([a, b]) is b


def test_haiku_test_retest_flip_resolves_to_confident_generation() -> None:
    # The audit's observed instability: identical input, conf 0.95 vs 0.0 with
    # opposite verdicts. The confident generation must hold the projection
    # regardless of arrival order.
    hi = _gen(insider=True, confidence=0.95, methods=2, when=1)
    zero = _gen(insider=False, confidence=0.0, methods=2, when=2)
    assert select_best_enrichment([hi, zero]) is hi
    assert select_best_enrichment([zero, hi]) is hi


def test_newer_schema_tier_beats_older_verbosity_and_confidence() -> None:
    """The Bruce v. Intuit lesson (2026-08-22): a sweep's calibrated v3 record
    must project over a verbose, overconfident v2 incumbent — else the sweep
    archives improvements that never reach the site."""
    v2_verbose = _gen(insider=True, confidence=0.95, methods=5, when=1, schema=2)
    v3_terse = _gen(insider=True, confidence=0.75, methods=1, when=2, schema=3)
    assert select_best_enrichment([v2_verbose, v3_terse]) is v3_terse
    assert select_best_enrichment([v3_terse, v2_verbose]) is v3_terse


def test_newer_schema_tier_owns_the_verdict() -> None:
    """Cross-schema confidences are incomparable: v2's inflated 0.95 must not
    outvote a calibrated v3 re-adjudication that flips the verdict."""
    v2_insider = _gen(insider=True, confidence=0.95, methods=3, when=1, schema=2)
    v3_noncase = _gen(insider=False, confidence=0.6, methods=0, when=2, schema=3)
    picked = select_best_enrichment([v2_insider, v3_noncase])
    assert picked is v3_noncase


def test_single_schema_tier_keeps_legacy_behavior() -> None:
    """All-v2 histories (untouched legacy rows) select exactly as before."""
    thin = _gen(insider=True, confidence=0.9, methods=1, when=2, schema=2)
    rich = _gen(insider=True, confidence=0.7, methods=4, when=1, schema=2)
    assert select_best_enrichment([thin, rich]) is rich


def test_single_generation_unchanged() -> None:
    only = _gen(insider=False, confidence=0.0, methods=0, note=None)
    assert select_best_enrichment([only]) is only
    assert select_best_enrichment([]) is None


# --- verbatim stamps ------------------------------------------------------------


def _forensics_with_quote(quote: str) -> PerCaseForensics:
    return PerCaseForensics(
        link="https://example.com/a",
        title="t",
        methods=[CaseMethod(action="a", evidence_quote=quote)],
    )


def test_stamp_true_for_exact_and_normalized_matches() -> None:
    source = 'She "downloaded  over 13,000 documents" — per the complaint.'
    exact = _forensics_with_quote("downloaded over 13,000 documents")
    stamp_quote_verbatim(exact, source)
    assert exact.methods[0].evidence_quote_verbatim is True

    # Typographic quotes/dashes and whitespace runs are forgiven.
    curly = _forensics_with_quote("she “downloaded over 13,000 documents” – per")
    stamp_quote_verbatim(curly, source)
    assert curly.methods[0].evidence_quote_verbatim is True


def test_stamp_false_for_paraphrase_and_none_for_empty() -> None:
    source = "The employee copied the customer list before resigning."
    para = _forensics_with_quote("the employee exfiltrated customer data")
    stamp_quote_verbatim(para, source)
    assert para.methods[0].evidence_quote_verbatim is False

    empty = _forensics_with_quote("")
    stamp_quote_verbatim(empty, source)
    assert empty.methods[0].evidence_quote_verbatim is None


# --- additive-field overlay (docs/schema-freeze-v4.md) --------------------------


def _gen_sector(
    *,
    sector: str | None,
    when: int,
    insider: bool = True,
    confidence: float = 0.8,
    methods: int = 2,
    schema: int = 3,
    model: str = "m",
) -> EnrichmentRecord:
    rec = _gen(insider=insider, confidence=confidence, methods=methods, when=when, schema=schema)
    rec.forensics.actor_employer_sector = sector
    rec.forensics.model = model
    return rec


def test_overlay_never_flips_verdict_or_confidence() -> None:
    """A low-confidence non-case that knows the sector cannot re-adjudicate."""
    winner = _gen_sector(sector=None, when=1, insider=True, confidence=0.9, methods=3)
    donor = _gen_sector(sector="financial-services", when=2, insider=False, confidence=0.2)
    history = [winner, donor]
    best = select_best_enrichment(history)
    assert best is winner
    out = project_additive_fields(history, best)
    assert out.is_insider_case is True
    assert out.confidence == 0.9
    assert out.actor_employer_sector == "financial-services"
    # The selected generation itself is untouched (copy, not mutation).
    assert winner.forensics.actor_employer_sector is None


def test_overlay_never_changes_methods_or_richness_inputs() -> None:
    winner = _gen_sector(sector=None, when=1, methods=3)
    donor = _gen_sector(sector="healthcare", when=2, methods=0, confidence=0.1)
    donor.ai_summary = None
    donor.forensics.outcome = "sentenced"
    out = project_additive_fields([winner, donor], winner)
    assert [m.action for m in out.methods] == [m.action for m in winner.forensics.methods]
    assert out.outcome == winner.forensics.outcome
    assert out.confidence == winner.forensics.confidence
    assert enrichment_richness(EnrichmentRecord(ai_summary=winner.ai_summary, forensics=out)) == (
        enrichment_richness(winner)
    )


def test_overlay_absent_when_no_generation_has_it() -> None:
    a = _gen_sector(sector=None, when=1)
    b = _gen_sector(sector=None, when=2, confidence=0.3)
    out = project_additive_fields([a, b], a)
    assert out is a.forensics  # no-op returns the same object
    assert out.actor_employer_sector is None
    assert out.actor_employer_sector_source is None


def test_overlay_picks_newest_same_tier_carrier() -> None:
    winner = _gen_sector(sector=None, when=5, confidence=0.95)
    older = _gen_sector(sector="technology", when=1, confidence=0.5, model="old")
    newer = _gen_sector(sector="professional-services", when=3, confidence=0.4, model="new")
    stale_tier = _gen_sector(sector="defense", when=9, schema=2, model="v2")
    out = project_additive_fields([older, stale_tier, winner, newer], winner)
    assert out.actor_employer_sector == "professional-services"
    assert out.actor_employer_sector_source == {
        "model": "new",
        "extracted_at": newer.forensics.extracted_at.isoformat(),
    }


def test_overlay_keeps_best_own_value_and_stamps_no_source() -> None:
    winner = _gen_sector(sector="retail", when=1)
    donor = _gen_sector(sector="energy", when=2, confidence=0.1)
    out = project_additive_fields([winner, donor], winner)
    assert out.actor_employer_sector == "retail"
    assert out.actor_employer_sector_source is None


def test_select_best_ordering_byte_identical_with_and_without_overlay() -> None:
    """On a history where nobody carries the field, the overlay is invisible."""
    history = [
        _gen_sector(sector=None, when=1, insider=True, confidence=0.7, methods=1),
        _gen_sector(sector=None, when=2, insider=False, confidence=0.6, methods=5),
        _gen_sector(sector=None, when=3, insider=True, confidence=0.7, methods=4),
    ]
    best = select_best_enrichment(history)
    plain = best.forensics.model_dump_json()
    overlaid = project_additive_fields(history, best).model_dump_json()
    assert plain == overlaid
    assert project_additive_fields(history, None) is None


def test_overlay_prefers_same_verdict_donor_then_falls_back() -> None:
    """D4: donors sharing best's verdict win even when an opposite-verdict one is newer."""
    winner = _gen_sector(sector=None, when=1, insider=True, confidence=0.9, methods=3)
    same = _gen_sector(sector="healthcare", when=2, insider=True, confidence=0.5, model="same")
    other = _gen_sector(sector="technology", when=3, insider=False, confidence=0.2, model="other")
    out = project_additive_fields([winner, same, other], winner)
    assert out.actor_employer_sector == "healthcare"
    assert out.actor_employer_sector_source["model"] == "same"
    # No same-verdict donor → any same-tier donor is acceptable.
    out2 = project_additive_fields([winner, other], winner)
    assert out2.actor_employer_sector == "technology"


def test_overlay_rejects_best_below_history_top_tier() -> None:
    """D5: the overlay tiers off HISTORY's top tier; a lower-tier best is a caller bug."""
    v3 = _gen_sector(sector=None, when=5, schema=3)
    v4 = _gen_sector(sector="retail", when=1, schema=4)
    with pytest.raises(ValueError):
        project_additive_fields([v4, v3], v3)
    # project_from_history can never hit it: it selects at the top tier itself.
    proj = project_from_history([v4, v3])
    assert proj.best is v4 and proj.forensics.actor_employer_sector == "retail"


def test_overlay_tie_and_none_extracted_at_are_deterministic() -> None:
    a = _gen_sector(sector="energy", when=2, confidence=0.5, model="a")
    b = _gen_sector(sector="defense", when=2, confidence=0.5, model="b")  # same stamp
    winner = _gen_sector(sector=None, when=9, confidence=0.9)
    first = project_additive_fields([winner, a, b], winner)
    for _ in range(5):
        assert project_additive_fields([winner, a, b], winner).model_dump_json() == (
            first.model_dump_json()
        )
    assert first.actor_employer_sector == "defense"  # later history position on a tie
    # None extracted_at: still deterministic, still stamped (with a null time).
    n1 = _gen_sector(sector="retail", when=1, model="n1")
    n1.forensics.extracted_at = None
    n2 = _gen_sector(sector="other", when=1, model="n2")
    n2.forensics.extracted_at = None
    out = project_additive_fields([winner, n1, n2], winner)
    assert out.actor_employer_sector == "other"
    assert out.actor_employer_sector_source == {"model": "n2", "extracted_at": None}


def test_projection_is_idempotent_and_survives_jsonl_round_trip() -> None:
    winner = _gen_sector(sector=None, when=1, insider=True, confidence=0.9, methods=3)
    donor = _gen_sector(sector="technology", when=2, insider=False, confidence=0.2)
    history = [winner, donor]
    p1 = project_from_history(history)
    p2 = project_from_history(history)
    assert p1.forensics.model_dump_json() == p2.forensics.model_dump_json()
    assert p1.case_record == p2.case_record
    # Neither call touched history or stamped anything into it.
    assert all(r.forensics.actor_employer_sector_source is None for r in history)
    assert winner.forensics.actor_employer_sector is None
    # Round-trip the history through JSON (what the JSONL store does) and re-project.
    reloaded = [EnrichmentRecord.model_validate(json.loads(r.model_dump_json())) for r in history]
    p3 = project_from_history(reloaded)
    assert p3.forensics.model_dump_json() == p1.forensics.model_dump_json()
    # The projected forensics itself round-trips with the stamp intact.
    back = PerCaseForensics.model_validate(json.loads(p1.forensics.model_dump_json()))
    assert back.actor_employer_sector == "technology"
    assert back.actor_employer_sector_source == p1.forensics.actor_employer_sector_source
    # The dedup signature carries the additive FIELD (a backfill that only
    # answers the field is a new generation) but never the provenance stamp.
    sig_projected = _enrichment_signature(
        EnrichmentRecord(ai_summary=winner.ai_summary, forensics=p1.forensics)
    )
    sig_unstamped = _enrichment_signature(
        EnrichmentRecord(
            ai_summary=winner.ai_summary,
            forensics=p1.forensics.model_copy(update={"actor_employer_sector_source": None}),
        )
    )
    assert sig_projected == sig_unstamped
    assert sig_projected[:-1] == _enrichment_signature(winner)[:-1]
    # Absent keys (pre-field rows) validate to None.
    d = json.loads(PerCaseForensics(link="x", title="t").model_dump_json())
    del d["actor_employer_sector"], d["actor_employer_sector_source"]
    assert PerCaseForensics.model_validate(d).actor_employer_sector is None


def test_dedupe_keeps_a_generation_that_differs_only_in_an_additive_field() -> None:
    """A field backfill that reproduces the stored generation on every other
    axis must still be appended — otherwise the row is marked landed with the
    field still null and the next dispatch bills it again (re-review finding).
    """
    from shared.schemas.forensics import append_enrichment

    base = _gen(insider=True, confidence=0.9, methods=4, when=1)
    same_but_filled = _gen(insider=True, confidence=0.9, methods=4, when=2)
    same_but_filled = EnrichmentRecord(
        ai_summary=same_but_filled.ai_summary,
        forensics=same_but_filled.forensics.model_copy(update={"actor_employer_sector": "retail"}),
    )
    history = append_enrichment([base], same_but_filled)
    assert len(history) == 2, "the paid generation was deduped away"
    # A true duplicate (field also equal) is still folded.
    assert len(append_enrichment(history, same_but_filled)) == 2
