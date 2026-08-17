"""ABOUT page (#/about) + footer-removal contracts.

The site footer is gone (operator call 2026-08-17: endless scrolling means
nobody ever saw it). Its organs moved: METHODOLOGY & COLOPHON folded into the
new ABOUT page, the GUIDE reopener moved to the mobile tab row (pinned in
test_site_guide), the lane-health line moved to SETTINGS with a
broken-only masthead chip, and the theme-select twin died (SETTINGS has the
picker). These are static-file regex checks in the test_site_guide /
test_matrix_data_sources style, plus node-executed unit tests for the pure
chip-presentation logic (skipped when no node runtime exists — CI carries one).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _index() -> str:
    return Path("web/index.html").read_text(encoding="utf-8")


def _app() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """Extract a top-level (2-space-indented) function body from web/app.js."""
    match = re.search(
        rf"\n  (?:async )?function {re.escape(name)}\(.*?\n  \}}",
        source,
        re.DOTALL,
    )
    assert match, f"{name}() not found in web/app.js — update this contract test"
    return match.group(0)


def _about_pane() -> str:
    match = re.search(r'<section class="pane pane-about-page".*?</section>', _index(), re.DOTALL)
    assert match, "ABOUT pane markup not found in web/index.html"
    return match.group(0)


# ── 1. The ABOUT page itself ────────────────────────────────────────────────


def test_about_pane_is_minimal() -> None:
    """Operator call (2026-08-17): ABOUT is name + GitHub + attribution, nothing
    else — no briefing sections, no lede, no live counts."""
    pane = " ".join(_about_pane().split())
    assert 'data-pane-panel="about"' in pane
    assert "<h2>ABOUT</h2>" in pane
    assert "Built and run by" in pane and "Tim Carreira" in pane
    for gone in (
        "WHERE THE DATA COMES FROM",
        "HOW FINDINGS ARE MADE",
        "READING THE SITE",
        "evp-section-head",
        "about-counts",
        "ii-about-facts",
    ):
        assert gone not in pane, f"ABOUT still carries {gone!r} — operator wants it minimal"



def test_about_byline_and_contact_are_github_only() -> None:
    pane = _about_pane()
    assert "Built and run by" in pane
    byline = re.search(
        r'<a href="https://github\.com/Scubber/insider-intel"[^>]*>\s*Tim Carreira',
        pane,
    )
    assert byline, "name must link to the public GitHub repo"
    # NO email address anywhere in the shipped UI (operator requirement).
    for blob in (_index(), _app()):
        assert "mailto:" not in blob
        assert "timothycarreira" not in blob.lower()
        assert "@gmail" not in blob.lower()


def test_about_carries_the_attribution_lines() -> None:
    """The ITM™/Forscie line moved here from the old colophon; data-source
    credit names CourtListener / Free Law Project."""
    pane = _about_pane()
    assert "Insider Threat Matrix™ © Forscie Limited" in pane
    assert "not affiliated" in pane
    assert "CourtListener" in pane
    assert "Free Law Project" in pane


def test_about_carries_no_corpus_numbers() -> None:
    """Sweep-dynamic invariant, minimal form: the static ABOUT markup may state
    the 6h cadence and nothing numeric about the corpus."""
    pane = re.sub(r"<[^>]+>", " ", _about_pane())
    digits = set(re.findall(r"\d+", pane))
    assert digits <= {"6"}, f"hardcoded number(s) {digits - {'6'}} in ABOUT markup"



def test_about_route_wiring() -> None:
    app = _app()
    # parseRoute handles #/about …
    parse = _fn_body(app, "parseRoute")
    assert '"/about"' in parse and '{ view: "about" }' in parse
    # … applyRoute dispatches it …
    apply_ = _fn_body(app, "applyRoute")
    assert re.search(r'route\.view === "about"[\s\S]{0,80}openAboutView\(\)', apply_)
    # … and a cold boot honors the deep link both before the API probe
    # (static prose must not wait) and after it (counts fill in).
    boot = _fn_body(app, "boot")
    assert boot.count("openAboutView()") == 2, (
        "boot() must dispatch #/about in the early (pre-probe) and post-probe passes"
    )
    opener = _fn_body(app, "openAboutView")
    assert 'setActivePane("about")' in opener
    assert 'navigate("/about")' in opener
    assert "fetch(" not in opener


def test_about_entry_points() -> None:
    """Two entry points: a quiet masthead link beside GUIDE (NOT a nav tab)
    and a line in the GUIDE panel."""
    html = _index()
    nav = re.search(r'<nav class="masthead-nav".*?</nav>', html, re.DOTALL)
    assert nav, "masthead nav not found"
    link = re.search(r'<a[^>]*id="about-open"[^>]*>', nav.group(0))
    assert link, "masthead ABOUT link missing"
    tag = link.group(0)
    assert 'href="#/about"' in tag
    assert "masthead-nav-item" not in tag, (
        "ABOUT must stay a quiet link — .masthead-nav-item's shared click handler switches panes"
    )
    assert "data-pane" not in tag
    # GUIDE panel line, visible in the panel body with a working link.
    guide = re.search(r'<section class="guide-panel".*?</section>', html, re.DOTALL)
    assert guide, "guide panel not found"
    assert 'id="guide-about-link"' in guide.group(0)
    assert 'href="#/about"' in guide.group(0)
    # Clicking it dismisses the guide so ABOUT isn't hidden underneath.
    wire = re.search(r"function wireGuide\(\).*?\}\)\(\);", _app(), re.DOTALL)
    assert wire and re.search(
        r'getElementById\("guide-about-link"\)[\s\S]{0,120}closeGuide\(\)', wire.group(0)
    ), "guide ABOUT link must close the guide panel on click"


# ── 2. The footer is gone, organs relocated ─────────────────────────────────


def test_footer_and_its_ids_are_gone() -> None:
    html = _index()
    app = _app()
    assert "<footer" not in html, "the site footer must not come back"
    for token in (
        "site-footer",
        "ii-footer",
        "footer-guide",
        "footer-about",
        "footer-settings",
        "footer-feed-link",
        "footer-lane-health",
        "footer-theme-select",
    ):
        assert token not in html, f"footer remnant {token!r} in web/index.html"
        assert token not in app, f"footer remnant {token!r} in web/app.js"
    # The theme picker's one home is SETTINGS.
    assert html.count('id="theme-select"') == 1
    # The feed link relocated to ABOUT and still points at the live API.
    assert 'id="about-feed-link"' in html
    assert re.search(r'getElementById\("about-feed-link"\)', app)
    assert "/feed.xml" in app


def test_lane_health_homes_are_settings_line_plus_masthead_chip() -> None:
    html = _index()
    app = _app()
    # SETTINGS: a collapsible DATA SOURCES section with the full line and a
    # teaching hint (what BROKEN means, when the line fills).
    settings = re.search(
        r'<section class="pane pane-settings".*?</main>',
        html,
        re.DOTALL,
    )
    assert settings, "settings pane not found"
    block = settings.group(0)
    # Key "lanes", deliberately NOT "sources" — the retired source-manager
    # section owned that key and ui_smoke pins it as removed.
    assert 'data-panel-key="lanes"' in block
    assert "DATA SOURCES" in block
    assert 'id="settings-lane-health"' in block
    assert "three failed or empty cycles" in block
    # Masthead: the warning chip, hidden by default.
    chip = re.search(r'<button[^>]*id="lane-warn"[^>]*>', html)
    assert chip, "masthead lane-warn chip missing"
    assert "hidden" in chip.group(0), "the chip must not render while healthy"
    # renderLaneHealth targets both homes via the pure presentation helper.
    render = _fn_body(app, "renderLaneHealth")
    assert 'getElementById("settings-lane-health")' in render
    assert 'getElementById("lane-warn")' in render
    assert "laneHealthPresentation(" in render
    # The chip routes to SETTINGS, where the full line lives.
    assert re.search(
        r'laneWarnChip\.addEventListener\("click"[\s\S]{0,120}setActivePane\("settings"\)',
        app,
    ), "lane-warn chip must open SETTINGS"
    # Chip color law: signal, not accent (observed/alleged tone for warnings).
    css = Path("web/styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.lane-warn\s*\{[^}]*var\(--signal\)", css)


# ── 3. Chip presentation logic, executed under node ─────────────────────────


def _node() -> str | None:
    for name in ("node", "node.exe", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _present(summary) -> dict | None:
    node = _node()
    if node is None:
        pytest.skip("no node runtime to execute the extracted presentation function")
    harness = (
        _fn_body(_app(), "laneHealthPresentation")
        + f"\nconst out = laneHealthPresentation({json.dumps(summary)});"
        + "\nprocess.stdout.write(JSON.stringify(out === undefined ? null : out));"
    )
    proc = subprocess.run([node, "-"], input=harness, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_chip_absent_without_telemetry() -> None:
    assert _present(None) is None
    assert _present({}) is None
    assert _present({"total": 0}) is None


def test_chip_absent_when_zero_broken() -> None:
    """The call-out that matters, without permanent chrome: healthy = no chip."""
    out = _present({"total": 5, "healthy": 5, "broken": 0, "broken_lanes": []})
    assert out is not None
    assert out["chip"] is None
    assert out["broken"] == 0
    assert out["line"] == "DATA SOURCES: 5 HEALTHY"


def test_chip_present_when_broken_with_names_in_tip() -> None:
    out = _present({"total": 6, "healthy": 4, "broken": 2, "broken_lanes": ["dead-a", "dead-b"]})
    assert out is not None
    assert out["line"] == "DATA SOURCES: 4 HEALTHY / 2 BROKEN (DEAD-A, DEAD-B)"
    assert out["chip"] is not None
    assert out["chip"]["text"] == "▲ 2 SOURCES BROKEN"
    assert "DEAD-A, DEAD-B" in out["chip"]["tip"]
    assert "SETTINGS" in out["chip"]["tip"]


def test_chip_singular_for_one_broken_lane() -> None:
    out = _present({"total": 3, "healthy": 2, "broken": 1, "broken_lanes": ["flaky"]})
    assert out is not None
    assert out["chip"]["text"] == "▲ 1 SOURCE BROKEN"
