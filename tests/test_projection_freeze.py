"""Projection freeze + verbatim grounding stamps (audit items D4/D5).

The selection projection must never let richness alone flip is_insider_case
(a verbose low-confidence generation re-adjudicating a case), and every
claimed evidence quote must carry a deterministic grounding verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime

from shared.schemas.forensics import (
    CaseMethod,
    EnrichmentRecord,
    PerCaseForensics,
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
