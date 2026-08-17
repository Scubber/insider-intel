"""Vendor sheet (#/tools/<slug>) + TOOLING table helper contracts.

Operator rebuild (2026-08-17): the NAMED TOOLS card-grid directory is GONE —
#/tooling is ONE grouped table (category group rows over tool rows with bare
counts), bare #/tools redirects to it, and #/tools/<slug> vendor sheets stay
as each product's court-filing record.

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
3. The web layer stays api()-only, the vendor sheet keeps its receipts shape
   (date | linked case title | INSIDER/CONTEXT badge, +N past the cap,
   teaching empty state), the sheet is deep-linkable, and bare #/tools
   redirects to the table (regex checks in the test_tooling style).
4. The pure client helpers (slug / directory build / table group + filter +
   toggle / count guard / column specs) are executed under node with
   synthetic fixtures (skipped when no node runtime exists — CI runners
   carry one).
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


# ── 3. Web contracts: routes, api()-only path, markup, chips ─────────────────


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


def test_tools_routes_vendor_sheet_stays_and_bare_tools_redirects() -> None:
    """#/tools/<slug> (vendor sheet) parses, applies, and hash-navigates;
    bare #/tools parses to the TOOLING table view (openToolingView then
    re-navigates to #/tooling) — the directory route is gone."""
    src = _app_js()
    parse = _fn_body(src, "parseRoute")
    assert '"/tools/"' in parse and "tools-vendor" in parse
    # Bare #/tools → the one table.
    bare = re.search(
        r'if \(path === "/tools" \|\| path === "/tools/"\) \{.*?\}',
        parse,
        re.DOTALL,
    )
    assert bare and 'view: "tooling"' in bare.group(0), "bare #/tools must redirect to #/tooling"
    assert '{ view: "tools" }' not in parse
    apply_route = _fn_body(src, "applyRoute")
    assert "openVendorToolView" in apply_route
    assert "openToolsView" not in src, "the NAMED TOOLS directory view survived the rebuild"
    # Opening a sheet navigates (hash history entry), never renders in place.
    assert "navigate(`/tools/" in _fn_body(src, "openVendorToolView")
    # Cold-load rule (narrowed 2026-08-17): every deep link lands where it
    # says — vendor sheet, category dossier, the table itself — and only the
    # BARE site root always starts on the stream.
    boot = _fn_body(src, "boot")
    assert "tools-vendor" in boot and "openVendorToolView" in boot
    assert 'route.view === "tooling"' in boot and "openToolingView()" in boot
    assert 'navigate("/")' not in boot.split('route.view === "tooling"')[1].split("} else if")[0]


def test_vendor_sheet_path_reads_only_the_live_api() -> None:
    """Every function on the vendor-sheet path renders from the session-cached
    /tooling payload — api() only, never fetch(); one live read
    (ensureTooling) serves the table, the dossiers, and the sheets alike."""
    src = _app_js()
    for name in (
        "vendorToolSlug",
        "buildVendorDirectory",
        "tlvCoverPhrase",
        "tlvCaseRow",
        "renderVendorSheet",
        "openVendorToolView",
        "openVendorTool",
        "showToolingSubview",
        "renderToolingBasis",
    ):
        assert "fetch(" not in _fn_body(src, name), f"{name}() fetches outside api()"
    assert "ensureTooling()" in _fn_body(src, "openVendorToolView")
    # The sheet is built from the payload's category rows client-side —
    # no second endpoint, no static file.
    assert "buildVendorDirectory(" in _fn_body(src, "renderVendorSheet")


def test_vendor_sheet_markup_reshaped() -> None:
    html = _index_html()
    # Back affordance goes to the one table, spelled that way.
    assert "← TOOLING" in html
    # Sheet shell: title, JS-built category context sub-line, stat row,
    # receipts TABLE, basis line.
    for el_id in ("tlv-view", "tlv-back", "tlv-title", "tlv-sub", "tlv-stats", "tlv-basis"):
        assert f'id="{el_id}"' in html, f"#{el_id} missing from the tooling pane"
    assert re.search(r'<table class="evp-table" id="tlv-cases">', html), (
        "vendor-sheet receipts must be an EVIDENCE-idiom table"
    )
    assert "NAMED IN THESE FILINGS" in html
    # The card grid, its controls, and the segmented switch are GONE.
    for gone in ("tld-view", "tld-grid", "tld-sort", "tld-category", "tld-filter", "tlp-switch"):
        assert f'id="{gone}"' not in html, f"deleted element #{gone} is back"
    assert "tld-card" not in html
    # The sheet's category context line + stat row are JS-built from the
    # payload: category label links to the dossier, coverage numbers are the
    # category's, the record below is the product's own.
    sheet = _fn_body(_app_js(), "renderVendorSheet")
    assert "category detects" in sheet
    assert "of observed insider behavior" in sheet
    assert "own court-filing record" in sheet
    assert "openToolingCategory(" in sheet
    # Stats come from the same swappable column-spec layer as the table.
    assert "toolingColumnSpecs()" in sheet


def test_vendor_sheet_receipts_pins() -> None:
    """Receipts shape (kept across the rebuild): source-linked case titles
    (target=_blank rel=noopener), INSIDER/CONTEXT badge, +N more past the
    25-ref cap, teaching empty state."""
    src = _app_js()
    sheet = _fn_body(src, "renderVendorSheet")
    assert "No case document names this product yet" in sheet
    assert "it appears here when one does" in sheet
    case_row = _fn_body(src, "tlvCaseRow")
    assert '"INSIDER"' in case_row and '"CONTEXT"' in case_row
    assert 'rel = "noopener"' in case_row
    assert 'target = "_blank"' in case_row
    # Receipt rows are table rows: date | linked title | badge.
    assert 'evpEl("tr")' in case_row
    assert "more named case" in sheet  # +N more beyond the 25-cap, spelled out
    assert "25 most recent" in sheet


def test_table_dossier_and_technique_chips_route_into_vendor_sheets() -> None:
    """Tool rows on the TOOLING table and the NAMED chips on category /
    technique dossiers are links into #/tools/<slug>, not inert spans."""
    src = _app_js()
    for name in ("renderToolingPage", "renderToolingCategory", "renderDossierTooling"):
        body = _fn_body(src, name)
        assert "openVendorTool(vendorToolSlug(v.name))" in body, (
            f"{name}() vendor links do not route into the sheet"
        )


def test_subview_bookkeeping_covers_exactly_three_views() -> None:
    """The TOOLING pane hosts exactly three sub-views now — table, category
    dossier, vendor sheet; the segmented switch and its bookkeeping are gone."""
    src = _app_js()
    subviews = re.search(r"const TOOLING_SUBVIEWS = \{.*?\};", src, re.DOTALL)
    assert subviews, "TOOLING_SUBVIEWS missing"
    block = subviews.group(0)
    for view_id in ("tlp-list-view", "tlc-view", "tlv-view"):
        assert view_id in block, f"{view_id} missing from TOOLING_SUBVIEWS"
    assert "tld-view" not in block
    assert "tlp-switch" not in src and "data-tooling-view" not in src


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


# TOOLING table fixture: named tools carry mentions; DLP's counts are chosen
# so INSIDER desc dominates CASES desc (Mid DLP has the most CASES but the
# fewest INSIDER); EDR is entirely unnamed.
_TABLE_CATS = [
    {
        "id": "dlp",
        "label": "DLP",
        "vendors": [
            {"name": "Alpha Guard", "mentions_cases": {"verdict_true": 0, "total": 0}},
            {"name": "Netskope", "mentions_cases": {"verdict_true": 2, "total": 3}},
            {"name": "Zeta DLP", "mentions_cases": {"verdict_true": 2, "total": 5}},
            {"name": "Mid DLP", "mentions_cases": {"verdict_true": 1, "total": 9}},
        ],
    },
    {
        "id": "casb",
        "label": "CASB",
        "vendors": [
            {"name": "Beta CASB", "mentions_cases": {"verdict_true": 1, "total": 1}},
            {"name": "Cloudlock", "mentions_cases": {"verdict_true": 0, "total": 0}},
        ],
    },
    {
        "id": "edr",
        "label": "EDR",
        "vendors": [
            {"name": "Watcher One", "mentions_cases": {"verdict_true": 0, "total": 0}},
        ],
    },
]

_TABLE_FNS = ["buildToolingTableGroups"]


def test_js_table_groups_named_first_insider_desc_then_cases_then_az() -> None:
    """Group rows keep the payload's category order; within a category the
    named tools lead (INSIDER desc, then CASES desc, then A–Z) and unnamed
    tools trail alphabetically — undimmed, but last."""
    got = _run_js(_TABLE_FNS, f'buildToolingTableGroups({json.dumps(_TABLE_CATS)}, "", false)')
    assert [g["id"] for g in got] == ["dlp", "casb", "edr"]
    dlp = got[0]
    assert dlp["label"] == "DLP"
    # Zeta (2 ins, 5 cases) > Netskope (2 ins, 3 cases) > Mid (1 ins, 9 cases
    # — INSIDER desc dominates CASES) > Alpha Guard (unnamed, alphabetical).
    assert [t["name"] for t in dlp["tools"]] == ["Zeta DLP", "Netskope", "Mid DLP", "Alpha Guard"]
    assert [t["name"] for t in got[1]["tools"]] == ["Beta CASB", "Cloudlock"]
    assert [t["name"] for t in got[2]["tools"]] == ["Watcher One"]


def test_js_table_text_filter_matches_tool_and_category_names() -> None:
    """The instant filter matches tool names AND category labels,
    case-insensitively; categories left with no matching tool drop out
    entirely (no orphan group rows)."""
    base = json.dumps(_TABLE_CATS)
    by_tool = _run_js(_TABLE_FNS, f'buildToolingTableGroups({base}, "netsk", false)')
    assert [(g["id"], [t["name"] for t in g["tools"]]) for g in by_tool] == [("dlp", ["Netskope"])]
    # A category-label hit keeps the whole group.
    by_label = _run_js(_TABLE_FNS, f'buildToolingTableGroups({base}, "casb", false)')
    assert [(g["id"], [t["name"] for t in g["tools"]]) for g in by_label] == [
        ("casb", ["Beta CASB", "Cloudlock"])
    ]
    assert _run_js(_TABLE_FNS, f'buildToolingTableGroups({base}, "no-such-tool", false)') == []


def test_js_table_court_filings_toggle_drops_unnamed_tools_and_empty_groups() -> None:
    """IN COURT FILINGS keeps only tools with ≥1 naming case; a category left
    with none (EDR) disappears with its group row."""
    got = _run_js(_TABLE_FNS, f'buildToolingTableGroups({json.dumps(_TABLE_CATS)}, "", true)')
    assert [(g["id"], [t["name"] for t in g["tools"]]) for g in got] == [
        ("dlp", ["Zeta DLP", "Netskope", "Mid DLP"]),
        ("casb", ["Beta CASB"]),
    ]
    # Toggle + filter compose.
    both = _run_js(_TABLE_FNS, f'buildToolingTableGroups({json.dumps(_TABLE_CATS)}, "dlp", true)')
    assert [(g["id"], [t["name"] for t in g["tools"]]) for g in both] == [
        ("dlp", ["Zeta DLP", "Netskope", "Mid DLP"])
    ]


def test_js_count_guard_zero_and_missing_render_as_em_dash() -> None:
    """The dangling-× regression, executed: zero/missing counts render as the
    muted em dash — never ×0, never a bare 0 formatted with a stray ×."""
    got = _run_js(
        ["toolingCountText"],
        '[0, null, undefined, "", 3, 25].map((n) => toolingCountText(n))',
    )
    assert got == ["—", "—", "—", "—", "3", "25"]


def test_js_column_specs_read_live_counts_and_guard_missing_shapes() -> None:
    """The swappable column layer: CASES reads mentions_cases.total, INSIDER
    reads mentions_cases.verdict_true, both fall back to 0 on missing shapes;
    INSIDER carries the --signal color class, CASES stays plain."""
    got = _run_js(
        ["toolingColumnSpecs"],
        "toolingColumnSpecs().map((c) => ["
        "c.key, c.label, c.statLabel, c.colorClass, "
        "c.value({mentions_cases: {total: 5, verdict_true: 2}}), "
        "c.value({mentions_cases: {}}), c.value({}), c.value(null)])",
    )
    assert got == [
        ["cases", "CASES", "NAMED", "", 5, 0, 0, 0],
        ["insider", "INSIDER", "INSIDER", "tlt-signal", 2, 0, 0, 0],
    ]
