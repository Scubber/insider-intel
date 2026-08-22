"""Operator-term chips: the vacuous-term filter stays wired (2026-08-22).

Pre-v3 enrichments emitted generic legal phrases ("confidential information")
as hunt terms; as copyable search chips they are noise. composeOperatorTerms
is the single choke point every chip render goes through — both the API
operator_terms branch and the client-composed fallback must filter through
isVacuousTerm. Mechanical regex pins in the test_site_guide style.
"""

from __future__ import annotations

import re
from pathlib import Path


def _app() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


def test_vacuous_term_set_exists_with_flagship_entries() -> None:
    app = _app()
    block = re.search(r"const VACUOUS_TERMS = new Set\(\[(.*?)\]\);", app, re.DOTALL)
    assert block, "VACUOUS_TERMS denylist missing from web/app.js"
    for phrase in ("confidential information", "trade secrets", "proprietary information"):
        assert f'"{phrase}"' in block.group(0), f"denylist lost {phrase!r}"


def test_both_term_branches_filter_through_the_denylist() -> None:
    app = _app()
    fn = re.search(r"function composeOperatorTerms\(article\) \{.*?\n  \}", app, re.DOTALL)
    assert fn, "composeOperatorTerms not found"
    body = fn.group(0)
    # API-supplied terms are filtered, not trusted.
    assert re.search(r"operator_terms.*?filter\(\(t\) => !isVacuousTerm\(t\)\)", body), (
        "API operator_terms branch no longer filters vacuous terms"
    )
    # The client-composed fallback rejects them at add().
    assert "isVacuousTerm(cleaned)" in body, (
        "client-composed add() no longer rejects vacuous terms"
    )
