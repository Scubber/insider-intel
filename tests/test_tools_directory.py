"""NAMED TOOLS directory (#/tools) + vendor sheet (#/tools/<slug>) contracts.

Operator decision (2026-08-17): named tooling gets a first-class directory
view inside the TOOLING pane — a modern card grid, one card per vendor across
every category — with the existing NAMED ×N chips on category/technique
dossiers routing into it, and a vendor sheet whose receipts are the ACTUAL
cases naming the product.

Four contracts pinned here:

1. Payload receipts: apps/search/vendor_mentions.py carries per-vendor case
   references (link, title, verdict flag, published) capped at the
   VENDOR_CASE_REFS_CAP most recent by published date with a ``more_cases``
   remainder — deterministic order, verdict flag mirroring the ledger gate,
   decoration-only (category ranking byte-identity is pinned in
   tests/test_tooling.py).
2. Vendor slugs: the JS slug rule (lowercase, non-alphanumeric runs → "-")
   yields a unique, non-empty slug for every display name in the checked-in
   aliases file — a new vendor whose name collides fails CI, not a router.
3. The web layer stays api()-only and both views are deep-linkable hash
   routes (regex checks in the test_tooling style over the shipped web/
   files), with the every-page-teaches-itself furniture: purpose sub-lines,
   tooltips, teaching empty states, GUIDE cheat line.
4. The pure client helpers (slug/build/sort/filter/covers) are executed
   under node with synthetic fixtures (skipped when no node runtime exists —
   CI runners carry one).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.search.vendor_mentions import (
    VENDOR_CASE_REFS_CAP,
    attach_vendor_mentions,
    load_vendor_aliases,
    scan_vendor_mentions,
    vendor_case_refs,
)

# ── 1. Payload: case receipts (pure, deterministic) ──────────────────────────

_VENDORS = [{"name": "AcmeSpy", "category": "irm", "aliases": ["AcmeSpy"]}]


def _row(link: str, text: str, *, title: str = "", published: str | None = None, verdict=True):
    return {
        "link": link,
        "clean_text": text,
        "title": title,
        "published": published,
        "verdict_true": verdict,
    }


def test_scan_carries_case_meta_for_matched_rows_only() -> None:
    """The scan's ``cases`` map holds display metadata (title/verdict/
    published) for rows that matched at least one vendor — and nothing for
    rows that matched none (the receipts stay bounded by real mentions)."""
    scan = scan_vendor_mentions(
        [
            _row("https://c/1", "AcmeSpy caught it.", title="Case One", published="2026-01-02"),
            _row("https://c/2", "no product named here"),
            _row("https://c/3", "AcmeSpy again.", verdict=False),
        ],
        _VENDORS,
    )
    assert set(scan["cases"]) == {"https://c/1", "https://c/3"}
    assert scan["cases"]["https://c/1"] == {
        "title": "Case One",
        "verdict_true": True,
        "published": "2026-01-02",
    }
    # Title falls back to the link; missing published stays None.
    assert scan["cases"]["https://c/3"] == {
        "title": "https://c/3",
        "verdict_true": False,
        "published": None,
    }


def test_case_refs_order_newest_first_undated_last_link_tiebreak() -> None:
    meta = {
        "https://c/old": {"title": "Old", "verdict_true": True, "published": "2024-05-01"},
        "https://c/new": {"title": "New", "verdict_true": False, "published": "2026-08-01"},
        "https://c/b-undated": {"title": "B", "verdict_true": True, "published": None},
        "https://c/a-undated": {"title": "A", "verdict_true": True, "published": None},
        "https://c/mid": {"title": "Mid", "verdict_true": True, "published": "2025-01-01"},
    }
    refs, more = vendor_case_refs(set(meta), meta)
    assert more == 0
    assert [r["link"] for r in refs] == [
        "https://c/new",
        "https://c/mid",
        "https://c/old",
        "https://c/a-undated",  # undated trail, deterministic by link
        "https://c/b-undated",
    ]
    assert refs[0] == {
        "link": "https://c/new",
        "title": "New",
        "verdict_true": False,
        "published": "2026-08-01",
    }


def test_case_refs_cap_keeps_most_recent_and_counts_remainder() -> None:
    """Cap contract: the head VENDOR_CASE_REFS_CAP (25) most recent by
    published date ride the payload; ``more`` counts the rest."""
    meta = {
        f"https://c/{n:03d}": {
            "title": f"Case {n}",
            "verdict_true": True,
            "published": f"2026-01-{(n % 28) + 1:02d}",
        }
        for n in range(30)
    }
    refs, more = vendor_case_refs(set(meta), meta)
    assert len(refs) == VENDOR_CASE_REFS_CAP == 25
    assert more == 5
    # Head is the newest date; the payload order is descending.
    dates = [r["published"] for r in refs]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == "2026-01-28"


def test_attach_decorates_each_vendor_with_capped_receipts() -> None:
    row = {"id": "irm", "examples": ["AcmeSpy"]}
    scan = scan_vendor_mentions(
        [
            _row(
                f"https://c/{n:03d}",
                "AcmeSpy present.",
                title=f"Case {n}",
                published=f"2026-02-{(n % 27) + 1:02d}",
            )
            for n in range(VENDOR_CASE_REFS_CAP + 3)
        ],
        _VENDORS,
    )
    attach_vendor_mentions([row], scan)
    vendor = row["vendors"][0]
    assert vendor["mentions_cases"]["total"] == VENDOR_CASE_REFS_CAP + 3
    assert len(vendor["cases"]) == VENDOR_CASE_REFS_CAP
    assert vendor["more_cases"] == 3
    # Receipt shape: exactly the four display fields, nothing corpus-heavy.
    assert set(vendor["cases"][0]) == {"link", "title", "verdict_true", "published"}


# ── 2. Vendor slugs: unique across the checked-in aliases file ───────────────


def _py_slug(name: str) -> str:
    """Python mirror of web/app.js vendorToolSlug — keep the two in sync."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


def test_every_alias_file_vendor_name_slugs_uniquely() -> None:
    names = sorted({v["name"] for v in load_vendor_aliases()["vendors"]})
    slugs = {}
    for name in names:
        slug = _py_slug(name)
        assert slug, f"empty slug for vendor name {name!r}"
        assert slug not in slugs, f"slug collision: {name!r} vs {slugs[slug]!r} → {slug!r}"
        slugs[slug] = name


# ── 3. Web contracts: routes, api()-only path, markup, chips, guide ──────────


def _index_html() -> str:
    return Path("web/index.html").read_text(encoding="utf-8")


def _app_js() -> str:
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


def test_tools_routes_registered_and_deep_linkable() -> None:
    """#/tools (directory) and #/tools/<slug> (vendor sheet) parse, apply,
    and hash-navigate — browser back walks sheet → directory → categories."""
    src = _app_js()
    parse = _fn_body(src, "parseRoute")
    assert '"/tools"' in parse and '"/tools/"' in parse
    assert '"tools"' in parse and "tools-vendor" in parse
    apply_route = _fn_body(src, "applyRoute")
    assert "openToolsView" in apply_route and "openVendorToolView" in apply_route
    # Opening either view navigates (hash history entry), never renders in
    # place — that's what makes browser back work.
    assert 'navigate("/tools")' in _fn_body(src, "openToolsView")
    assert "navigate(`/tools/" in _fn_body(src, "openVendorToolView")
    # A vendor-sheet deep link is honored at boot (specific shared content);
    # a bare #/tools takeover reverts to the stream like #/tooling does.
    boot = _fn_body(src, "boot")
    assert "tools-vendor" in boot
    assert 'route.view === "tools"' in boot


def test_directory_path_reads_only_the_live_api() -> None:
    """Every function on the directory / vendor-sheet path renders from the
    session-cached /tooling payload — api() only, never fetch(); one live
    read (ensureTooling) serves categories, directory, and sheets alike."""
    src = _app_js()
    for name in (
        "vendorToolSlug",
        "buildVendorDirectory",
        "sortVendorDirectory",
        "filterVendorDirectory",
        "tldCoverBit",
        "tldCoversSummary",
        "syncTldCategoryOptions",
        "tldCard",
        "renderToolsDirectory",
        "loadToolsDirectory",
        "openToolsView",
        "tlvCaseRow",
        "renderVendorSheet",
        "openVendorToolView",
        "openVendorTool",
        "showToolingSubview",
        "renderToolingBasis",
    ):
        assert "fetch(" not in _fn_body(src, name), f"{name}() fetches outside api()"
    assert "ensureTooling()" in _fn_body(src, "loadToolsDirectory")
    assert "ensureTooling()" in _fn_body(src, "openVendorToolView")
    # The directory is built from the payload's category rows client-side —
    # no second endpoint, no static file.
    assert "buildVendorDirectory(" in _fn_body(src, "renderToolsDirectory")
    assert "buildVendorDirectory(" in _fn_body(src, "renderVendorSheet")


def test_segmented_switch_and_directory_markup_present() -> None:
    html = _index_html()
    # Segmented switch: house pill idiom, both views labeled, tooltips on.
    switch = re.search(r'<div class="tlp-switch" id="tlp-switch".*?</div>', html, re.DOTALL)
    assert switch, "CATEGORIES | NAMED TOOLS switch missing"
    assert 'data-tooling-view="categories"' in switch.group(0)
    assert 'data-tooling-view="tools"' in switch.group(0)
    assert ">CATEGORIES<" in switch.group(0) and ">NAMED TOOLS<" in switch.group(0)
    assert switch.group(0).count("data-tip=") == 2
    # Directory view: purpose sub-line, sort pills, category select, text
    # filter, grid, basis line.
    for el_id in ("tld-view", "tld-sort", "tld-category", "tld-filter", "tld-grid", "tld-basis"):
        assert f'id="{el_id}"' in html, f"#{el_id} missing from the tooling pane"
    assert 'data-tld-sort="named"' in html and 'data-tld-sort="az"' in html
    # Vendor sheet: back affordance, title, stats, category links, receipts.
    for el_id in (
        "tlv-view",
        "tlv-back",
        "tlv-title",
        "tlv-stats",
        "tlv-cats",
        "tlv-cases",
        "tlv-basis",
    ):
        assert f'id="{el_id}"' in html, f"#{el_id} missing from the tooling pane"
    assert "NAMED IN THESE CASES" in html
    # Purpose sub-lines say what question each view answers.
    assert "one card per vendor" in html
    assert "One named product against the stored case record" in html
    # Presence-not-effectiveness framing rides both views' shells.
    assert "never an effectiveness score" in html


def test_directory_teaches_itself() -> None:
    """Teaching furniture: ×0 vendors dim under an honest divider, empty
    states say what appears and how, tooltips carry the methodology."""
    src = _app_js()
    render = _fn_body(src, "renderToolsDirectory")
    assert '"NOT YET NAMED IN CASES"' in render  # honest, not hidden
    assert "tld-dim" in _fn_body(src, "tldCard")
    assert "No product matches this filter" in render  # teaching empty state
    sheet = _fn_body(src, "renderVendorSheet")
    assert "No case document names this product yet" in sheet
    assert "it appears here when one does" in sheet
    # Receipts rows: verdict badge + source-link idiom + cap explainer.
    case_row = _fn_body(src, "tlvCaseRow")
    assert '"VERDICT-TRUE"' in case_row and '"CONTEXT"' in case_row
    assert 'rel = "noopener"' in case_row
    assert "more named case" in sheet  # +N more beyond the 25-cap, spelled out
    assert "25 most recent" in sheet


def test_dossier_and_category_chips_route_into_vendor_sheets() -> None:
    """The NAMED ×N chips — TOOLING list detail, category dossier, technique
    dossier — are links into #/tools/<slug>, not inert spans."""
    src = _app_js()
    for name in ("renderToolingPage", "renderToolingCategory", "renderDossierTooling"):
        body = _fn_body(src, name)
        assert "openVendorTool(vendorToolSlug(v.name))" in body, (
            f"{name}() vendor chips do not route into the directory"
        )


def test_guide_and_switch_cover_both_views() -> None:
    html = _index_html()
    cheat = re.search(r'id="guide-cheat".*?</dl>', html, re.DOTALL)
    assert cheat and "NAMED TOOLS" in cheat.group(0), (
        "GUIDE cheat line no longer teaches the NAMED TOOLS view"
    )
    # Sub-view bookkeeping: exactly one of the four views shows; the switch
    # hides on detail views (category dossier / vendor sheet).
    sub = _fn_body(_app_js(), "showToolingSubview")
    for view_id in ("tlp-list-view", "tlc-view", "tld-view", "tlv-view"):
        assert view_id in _app_js(), f"{view_id} missing"
    assert 'which === "category" || which === "vendor"' in sub


# ── 4. Pure client helpers under node (synthetic fixtures) ───────────────────


def _node() -> str | None:
    for name in ("node", "node.exe", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_js(fn_names: list[str], call: str) -> object:
    node = _node()
    if node is None:
        pytest.skip("no node runtime to execute the extracted helpers")
    src = _app_js()
    harness = (
        "\n".join(_fn_body(src, name) for name in fn_names)
        + f"\nprocess.stdout.write(JSON.stringify({call}));"
    )
    proc = subprocess.run([node, "-"], input=harness, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


_CATS = [
    {
        "id": "dlp",
        "label": "DLP",
        "detection_coverage_pct": 64,
        "prevention_coverage_pct": 41,
        "detect_volume": 640,
        "prevent_volume": 410,
        "vendors": [
            {
                "name": "Netskope",
                "mentions_cases": {"verdict_true": 2, "total": 3},
                "cases": [
                    {
                        "link": "https://c/1",
                        "title": "One",
                        "verdict_true": True,
                        "published": "2026-01-01",
                    },
                ],
                "more_cases": 0,
            },
            {
                "name": "Alpha DLP",
                "mentions_cases": {"verdict_true": 0, "total": 0},
                "cases": [],
                "more_cases": 0,
            },
        ],
    },
    {
        "id": "casb",
        "label": "CASB",
        "detection_coverage_pct": None,
        "prevention_coverage_pct": None,
        "detect_volume": 3,
        "prevent_volume": 0,
        "vendors": [
            {
                "name": "Netskope",
                "mentions_cases": {"verdict_true": 2, "total": 3},
                "cases": [
                    {
                        "link": "https://c/1",
                        "title": "One",
                        "verdict_true": True,
                        "published": "2026-01-01",
                    },
                ],
                "more_cases": 0,
            },
            {
                "name": "Beta CASB",
                "mentions_cases": {"verdict_true": 1, "total": 1},
                "cases": [
                    {
                        "link": "https://c/2",
                        "title": "Two",
                        "verdict_true": True,
                        "published": "2026-02-01",
                    },
                ],
                "more_cases": 0,
            },
        ],
    },
]


def test_js_slug_rule_matches_python_mirror_on_real_names() -> None:
    names = sorted({v["name"] for v in load_vendor_aliases()["vendors"]})
    sample = names + ["Splunk UBA", "Micro Focus (ArcSight)", "  Weird -- Name  "]
    got = _run_js(["vendorToolSlug"], f"{json.dumps(sample)}.map(vendorToolSlug)")
    assert got == [_py_slug(n) for n in sample]


def test_js_build_dedupes_dual_homed_vendors() -> None:
    """One card per vendor: Netskope (dlp + casb) becomes ONE entry carrying
    both category tags, MAX-merged counts (identical alias sets report the
    same distinct cases — never summed), and link-deduped receipts."""
    got = _run_js(
        ["vendorToolSlug", "buildVendorDirectory"],
        f"buildVendorDirectory({json.dumps(_CATS)})",
    )
    by_name = {e["name"]: e for e in got}
    assert set(by_name) == {"Netskope", "Alpha DLP", "Beta CASB"}
    net = by_name["Netskope"]
    assert net["slug"] == "netskope"
    assert [c["id"] for c in net["categories"]] == ["dlp", "casb"]
    assert net["mentions"] == {"verdict_true": 2, "total": 3}  # MAX, not 6
    assert [c["link"] for c in net["cases"]] == ["https://c/1"]  # deduped
    assert net["more_cases"] == 0


def test_js_sort_modes() -> None:
    fns = ["vendorToolSlug", "buildVendorDirectory", "sortVendorDirectory"]
    named = _run_js(fns, f'sortVendorDirectory(buildVendorDirectory({json.dumps(_CATS)}), "named")')
    # NAMED ×N default: total desc, verdict-true desc, then A–Z — ×0 last.
    assert [e["name"] for e in named] == ["Netskope", "Beta CASB", "Alpha DLP"]
    az = _run_js(fns, f'sortVendorDirectory(buildVendorDirectory({json.dumps(_CATS)}), "az")')
    assert [e["name"] for e in az] == ["Alpha DLP", "Beta CASB", "Netskope"]


def test_js_filter_by_category_and_text() -> None:
    fns = ["vendorToolSlug", "buildVendorDirectory", "filterVendorDirectory"]
    base = f"buildVendorDirectory({json.dumps(_CATS)})"
    only_casb = _run_js(fns, f'filterVendorDirectory({base}, "casb", "")')
    assert sorted(e["name"] for e in only_casb) == ["Beta CASB", "Netskope"]
    # Text matches product names AND category labels, case-insensitively.
    text_name = _run_js(fns, f'filterVendorDirectory({base}, "", "netsk")')
    assert [e["name"] for e in text_name] == ["Netskope"]
    text_label = _run_js(fns, f'filterVendorDirectory({base}, "", "dlp")')
    assert sorted(e["name"] for e in text_label) == ["Alpha DLP", "Netskope"]
    both = _run_js(fns, f'filterVendorDirectory({base}, "dlp", "beta")')
    assert both == []


def test_js_covers_summary_obeys_small_n_law() -> None:
    """The card's covers line: percentages when the floor is met, ×N obs.
    when suppressed — per category, joined for dual-homed vendors."""
    got = _run_js(
        ["vendorToolSlug", "buildVendorDirectory", "tldCoverBit", "tldCoversSummary"],
        f"buildVendorDirectory({json.dumps(_CATS)}).map((e) => [e.name, tldCoversSummary(e)])",
    )
    lines = dict(got)
    assert lines["Alpha DLP"] == "DLP: DETECTS 64% · PREVENTS 41%"
    assert lines["Beta CASB"] == "CASB: DETECTS ×3 obs. · PREVENTS ×0 obs."
    assert lines["Netskope"] == (
        "DLP: DETECTS 64% · PREVENTS 41%  /  CASB: DETECTS ×3 obs. · PREVENTS ×0 obs."
    )
