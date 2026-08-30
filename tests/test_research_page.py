"""RESEARCH page (#/research) contracts.

RESEARCH is the one sanctioned home for frozen, corpus-derived numbers on the
site: authored, dated briefings published by merge, each stamped with an
AS OF corpus dateline and pointing at EVIDENCE for the live figures. Static
prose in the ABOUT-pane style — app.js only routes and shows/hides, no
network. These are static-file regex checks in the test_about_page style.
"""

from __future__ import annotations

import re
from pathlib import Path

SLUGS = ("danger-profiles-2026-08", "email-destinations-2026-08")


def _index() -> str:
    return Path("web/index.html").read_text(encoding="utf-8")


def _app() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    match = re.search(
        rf"\n  (?:async )?function {re.escape(name)}\(.*?\n  \}}",
        source,
        re.DOTALL,
    )
    assert match, f"{name}() not found in web/app.js — update this contract test"
    return match.group(0)


def _research_pane() -> str:
    match = re.search(
        r'<section class="pane pane-research-page".*?</section>', _index(), re.DOTALL
    )
    assert match, "RESEARCH pane markup not found in web/index.html"
    return match.group(0)


def _briefing(slug: str) -> str:
    match = re.search(
        rf'<article class="research-briefing" data-briefing="{slug}".*?</article>',
        _index(),
        re.DOTALL,
    )
    assert match, f"briefing article {slug!r} not found in web/index.html"
    return match.group(0)


# ── Route wiring ────────────────────────────────────────────────────────────


def test_research_route_wiring() -> None:
    app = _app()
    parse = _fn_body(app, "parseRoute")
    assert '"/research"' in parse and '{ view: "research" }' in parse
    assert '"/research/"' in parse
    assert '"research-briefing"' in parse
    apply_ = _fn_body(app, "applyRoute")
    assert re.search(r'route\.view === "research"[\s\S]{0,80}openResearchView\(\)', apply_)
    assert re.search(
        r'route\.view === "research-briefing"[\s\S]{0,120}openResearchView\(route\.id\)', apply_
    )
    # Boot dispatch: the early (pre-probe) pass AND the post-probe pass — a
    # static-prose pane must never wait out the API probe (the EVIDENCE lesson).
    boot = _fn_body(app, "boot")
    assert boot.count("openResearchView(") >= 2, (
        "boot() must dispatch #/research in the early (pre-probe) and post-probe passes"
    )


def test_research_opener_is_static() -> None:
    opener = _fn_body(_app(), "openResearchView")
    assert 'setActivePane("research")' in opener
    assert "navigate(" in opener and "/research" in opener
    assert "fetch(" not in opener, "RESEARCH is static prose — no network in the opener"


def test_research_is_a_registered_takeover_pane() -> None:
    app = _app()
    panes = re.search(r"const PANES = new Set\(\[(.*?)\]\)", app)
    takeover = re.search(r"const TAKEOVER_PANES = new Set\(\[(.*?)\]\)", app)
    assert panes and '"research"' in panes.group(1)
    assert takeover and '"research"' in takeover.group(1)


# ── Markup ──────────────────────────────────────────────────────────────────


def test_research_entry_points() -> None:
    html = _index()
    nav = re.search(r'<nav class="masthead-nav".*?</nav>', html, re.DOTALL)
    assert nav and 'data-pane="research"' in nav.group(0), "masthead RESEARCH tab missing"
    mobile = re.search(r'<nav class="mobile-tabs".*?</nav>', html, re.DOTALL)
    assert mobile and 'data-pane="research"' in mobile.group(0), "mobile RESEARCH tab missing"
    cheat = re.search(r'id="guide-cheat".*?</dl>', html, re.DOTALL)
    assert cheat and "<dt>RESEARCH</dt>" in cheat.group(0), "guide cheat-sheet line missing"


def test_research_pane_structure() -> None:
    pane = _research_pane()
    assert 'data-pane-panel="research"' in pane
    assert "<h2>RESEARCH</h2>" in pane
    assert 'class="research-index"' in pane
    for slug in SLUGS:
        assert f'href="#/research/{slug}"' in pane, f"index card link for {slug} missing"
        _briefing(slug)


def test_briefings_carry_the_frozen_snapshot_contract() -> None:
    """Every briefing states its AS OF corpus dateline, admits its limits, and
    links to EVIDENCE for the live numbers — frozen-snapshot honesty."""
    for slug in SLUGS:
        art = _briefing(slug)
        meta = re.search(r'<p class="research-meta">(.*?)</p>', art, re.DOTALL)
        assert meta, f"{slug}: mono dateline missing"
        dateline = " ".join(meta.group(1).split())
        assert "AS OF" in dateline, f"{slug}: dateline lacks AS OF"
        assert "corpus" in dateline.lower(), f"{slug}: dateline lacks the corpus basis"
        assert "PUBLISHED" in dateline, f"{slug}: dateline lacks PUBLISHED"
        assert re.search(r"(?i)<h4>\s*limits\s*</h4>", art), f"{slug}: LIMITS section missing"
        assert '#/evidence' in art, f"{slug}: no link to the live EVIDENCE page"


def test_briefing_slugs_are_smoke_deep_links() -> None:
    smoke = Path("scripts/ui_smoke_ci.py").read_text(encoding="utf-8")
    assert "#/research" in smoke
    assert "#/research/danger-profiles-2026-08" in smoke
    assert '"research": ".pane-research-page"' in smoke
    order = re.search(r"PANE_ORDER = \((.*?)\)", smoke, re.DOTALL)
    assert order and '"research"' in order.group(1)


# ── Styles ──────────────────────────────────────────────────────────────────


def test_research_pane_visibility_css() -> None:
    css = Path("web/styles.css").read_text(encoding="utf-8")
    assert '.app-shell[data-pane="research"] .pane-research-page' in css
    assert '.app-shell[data-pane="research"] .pane-grid' in css
    assert re.search(r"\.pane-research-page \{\s*display: none;", css), (
        "RESEARCH pane must be hidden outside its own pane state"
    )
