"""RESEARCH briefings are PARKED (operator decision 2026-09-05).

The site has no RESEARCH tab, pane, or route. The four briefings live as
drafts in docs/research/drafts/ — still the single sanctioned home for
frozen numbers, each with an AS OF dateline — until they are rewritten and
republished per docs/research/README.md. This file pins the parking:
nothing in web/ renders a briefing, old #/research links stay harmless,
and every draft keeps its dateline + Limits contract while it waits.
"""

from __future__ import annotations

import re
from pathlib import Path

DRAFTS = Path("docs/research/drafts")

SLUGS = (
    "fs-insider-profiles-2026-10",
    "fs-insider-profiles-2026-09",
    "danger-profiles-2026-08",
    "email-destinations-2026-08",
)


def _index() -> str:
    return Path("web/index.html").read_text(encoding="utf-8")


def _app() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


# ── Nothing ships in web/ ───────────────────────────────────────────────────


def test_index_has_no_research_surface() -> None:
    html = _index()
    for needle in ('data-pane="research"', "pane-research-page", "research-briefing", "#/research"):
        assert needle not in html, f"web/index.html still carries {needle!r} — RESEARCH is parked"
    cheat = re.search(r'id="guide-cheat".*?</dl>', html, re.DOTALL)
    assert cheat and "<dt>RESEARCH</dt>" not in cheat.group(0), "GUIDE still teaches a parked tab"


def test_app_has_no_research_opener_or_pane() -> None:
    app = _app()
    assert "openResearchView" not in app
    panes = re.search(r"const PANES = new Set\(\[(.*?)\]\)", app)
    takeover = re.search(r"const TAKEOVER_PANES = new Set\(\[(.*?)\]\)", app)
    assert panes and '"research"' not in panes.group(1)
    assert takeover and '"research"' not in takeover.group(1)
    for css in ("web/styles.css", "web/themes.css"):
        text = Path(css).read_text(encoding="utf-8")
        assert ".research-" not in text and 'data-pane="research"' not in text, css


def test_legacy_research_links_land_on_the_stream() -> None:
    """Old #/research and #/research/<slug> links must never crash: the
    router maps them to the stream and rewrites the hash to the root."""
    app = _app()
    parse = re.search(r"\n  function parseRoute\(.*?\n  \}", app, re.DOTALL)
    assert parse, "parseRoute() not found — update this contract test"
    body = parse.group(0)
    assert re.search(
        r'path === "/research" \|\| path\.startsWith\("/research/"\)[\s\S]{0,300}'
        r'return \{ view: "stream", legacy: "/" \}',
        body,
    )
    assert "research-briefing" not in body
    assert "if (route.legacy) navigate(route.legacy);" in app
    assert "if (early.legacy) navigate(early.legacy);" in app


def test_smoke_keeps_one_legacy_research_link() -> None:
    smoke = Path("scripts/ui_smoke_ci.py").read_text(encoding="utf-8")
    assert 'LEGACY_RESEARCH_LINK = "#/research/fs-insider-profiles-2026-09"' in smoke
    assert smoke.count("LEGACY_RESEARCH_LINK,") == 2, "both deep-link lists carry the legacy link"
    assert '"research"' not in smoke
    assert "pane-research-page" not in smoke


# ── Drafts keep their contract while parked ────────────────────────────────


def test_expected_drafts_exist() -> None:
    found = sorted(p.stem for p in DRAFTS.glob("*.html"))
    assert found == sorted(SLUGS), f"drafts on disk {found} != expected {sorted(SLUGS)}"
    assert (DRAFTS.parent / "README.md").exists()


def test_drafts_carry_parked_header_and_frozen_snapshot_contract() -> None:
    for slug in SLUGS:
        text = (DRAFTS / f"{slug}.html").read_text(encoding="utf-8")
        header = re.match(r"<!--(.*?)-->", text, re.DOTALL)
        assert header, f"{slug}: PARKED header comment missing"
        head = header.group(1)
        assert f"slug: {slug}" in head
        assert "PARKED 2026-09-05" in head and "rewrite before republishing" in head
        assert "PUBLISHED" in head and "AS OF" in head, f"{slug}: header dateline incomplete"
        assert "index card title:" in head and "index card summary:" in head
        assert f'<article class="research-briefing" data-briefing="{slug}">' in text
        meta = re.search(r'<p class="research-meta">(.*?)</p>', text, re.DOTALL)
        assert meta, f"{slug}: dateline missing"
        dateline = " ".join(meta.group(1).split())
        assert "PUBLISHED" in dateline and "AS OF" in dateline and "corpus" in dateline.lower()
        assert re.search(r"(?i)<h4>\s*limits\s*</h4>", text), f"{slug}: Limits section missing"
        assert text.rstrip().endswith("</article>")
