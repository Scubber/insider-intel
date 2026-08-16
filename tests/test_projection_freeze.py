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
) -> EnrichmentRecord:
    return EnrichmentRecord(
        ai_summary=note,
        forensics=PerCaseForensics(
            link="https://example.com/a",
            title="t",
            is_insider_case=insider,
            confidence=confidence,
            methods=[CaseMethod(action=f"a{i}") for i in range(methods)],
            extracted_at=datetime(2026, 1, when, tzinfo=UTC),
        ),
    )


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
