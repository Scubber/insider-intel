"""Site guide web contract: first-visit intro + GUIDE reopener stay wired.

Static-file regex checks in the test_boot_snapshot style — the guide is the
onboarding layer for the guidance product (BUILD A PROGRAM / DETECT / PREVENT /
HUNT), so its panel, its reopen affordances, and its dismiss persistence are
pinned here rather than left to manual QA.
"""

from __future__ import annotations

import re
from pathlib import Path

GUIDE_KEY = "insider-intel-guide-dismissed"


def _index() -> str:
    return Path("web/index.html").read_text(encoding="utf-8")


def _app() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


def test_guide_panel_present_and_hidden_by_default() -> None:
    html = _index()
    panel = re.search(r'<section class="guide-panel" id="guide-panel"[^>]*>', html)
    assert panel, "guide panel missing from web/index.html"
    assert "hidden" in panel.group(0), "guide panel must not flash before app.js decides"
    # Dismiss + expand affordances live inside the panel.
    for el_id in ("guide-dismiss", "guide-cheat", "guide-more", "guide-gotit"):
        assert f'id="{el_id}"' in html, f"#{el_id} missing from the guide panel"
    # The four jobs of the guidance product, verbatim.
    for job in ("BUILD A PROGRAM", "DETECT", "PREVENT", "HUNT"):
        assert re.search(rf"<dt>{job}</dt>", html), f"job label {job} missing"
    # Trust line: court-filing citations + enrichment provenance.
    assert "cited court filings" in html
    assert "provenance is stamped on each card" in html


def test_guide_reopen_affordances_present() -> None:
    html = _index()
    # Masthead GUIDE sits inside the nav but must NOT carry .masthead-nav-item —
    # that class's shared click handler switches panes (this exact trap is
    # documented inline in index.html).
    nav = re.search(r'<nav class="masthead-nav".*?</nav>', html, re.DOTALL)
    assert nav, "masthead nav not found"
    guide_btn = re.search(r'<button[^>]*id="guide-open"[^>]*>', nav.group(0))
    assert guide_btn, "masthead GUIDE button missing"
    assert "masthead-nav-item" not in guide_btn.group(0)
    assert 'aria-controls="guide-panel"' in guide_btn.group(0)
    # Mobile reopener keeps the guide reachable on phones, where the masthead
    # nav is display:none: a GUIDE button in the mobile tab row (the footer
    # reopener died with the footer, 2026-08-17). It must NOT carry data-pane —
    # the shared mobile-tab click handler switches panes.
    mobile = re.search(r'<nav class="mobile-tabs".*?</nav>', html, re.DOTALL)
    assert mobile, "mobile tab row not found"
    mobile_guide = re.search(r'<button[^>]*id="mobile-guide"[^>]*>', mobile.group(0))
    assert mobile_guide, "mobile GUIDE tab missing from the mobile tab row"
    assert "data-pane" not in mobile_guide.group(0), (
        "mobile GUIDE must not carry data-pane — it opens the guide, not a pane"
    )
    # And app.js must wire it alongside the masthead button.
    app = _app()
    wire = re.search(r"function wireGuide\(\).*?\}\)\(\);", app, re.DOTALL)
    assert wire and 'getElementById("mobile-guide")' in wire.group(0), (
        "wireGuide() lost the mobile-tab GUIDE reopener"
    )


def test_guide_cheat_sheet_covers_every_masthead_tab() -> None:
    """Every top-level tab must have a cheat-sheet line — adding a nav pane
    without teaching the guide about it is exactly the drift this pins."""
    html = _index()
    nav = re.search(r'<nav class="masthead-nav".*?</nav>', html, re.DOTALL)
    assert nav
    panes = set(re.findall(r'data-pane="([a-z]+)"', nav.group(0)))
    assert panes, "no data-pane buttons in the masthead nav — update this contract test"
    cheat = re.search(r'id="guide-cheat".*?</dl>', html, re.DOTALL)
    assert cheat, "guide cheat sheet <dl> not found"
    labels = {"articles": "STREAM"}  # nav label differs from the pane id
    for pane in panes:
        label = labels.get(pane, pane.upper())
        assert f"<dt>{label}</dt>" in cheat.group(0), (
            f"masthead tab {label} has no cheat-sheet line in the guide"
        )


def test_guide_dismiss_persists_and_resets() -> None:
    app = _app()
    assert f'"{GUIDE_KEY}"' in app, "dismiss persistence key missing from web/app.js"
    # Dismissal is written, not just read.
    assert re.search(r"localStorage\.setItem\(\s*GUIDE_DISMISSED_KEY", app)
    # RESET PREFERENCES promises "reload as a first-time visitor" — the guide
    # key must be in its clear list (the literal appears alongside the other
    # insider-intel-* keys there).
    reset = re.search(r"els\.resetPrefs.*?location\.reload\(\)", app, re.DOTALL)
    assert reset, "reset-prefs handler not found — update this contract test"
    assert GUIDE_KEY in reset.group(0), "guide key missing from the reset-prefs clear list"


def test_guide_wiring_esc_and_first_visit() -> None:
    app = _app()
    wire = re.search(r"function wireGuide\(\).*?\}\)\(\);", app, re.DOTALL)
    assert wire, "wireGuide() not found in web/app.js — update this contract test"
    block = wire.group(0)
    # Esc dismisses.
    assert '"Escape"' in block
    # First visit auto-opens the short intro, gated on the dismissed key.
    assert re.search(r"if \(!guideDismissed\(\)\) openGuide\(false\)", block)
    # Guide layer stays static: no network fetch may sneak into it, so it
    # keeps working in snapshot-only mode and never delays first paint.
    assert "fetch(" not in block
