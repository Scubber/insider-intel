"""TOOLING page: curated category → DT/PV mapping + sweep-fresh coverage ranking.

Three contracts pinned here:

1. The checked-in taxonomy (shared/data/tooling_map.json) validates against
   the packaged ITM catalog — no dangling control ids, ids single-homed —
   so an ITM catalog refresh (itm-refresh.yml) flags mapping drift in CI.
2. The ranking math (apps/search/tooling.py::rank_tool_categories) is a pure,
   deterministic function unit-tested with synthetic ledger/counts fixtures,
   and GET /tooling recomputes it from the in-memory index per call (same
   sweep-propagation contract the matrix data-source tests pin).
3. The web layer stays api()-only: mechanical regex checks over the shipped
   web/ files in the test_site_guide / test_matrix_data_sources style — a
   static-file read creeping into the TOOLING path fails CI, not a review.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search import vendor_mentions as vendor_mentions_module
from apps.search.api import app
from apps.search.tooling import load_tooling_map, rank_tool_categories
from apps.search.vendor_mentions import attach_vendor_mentions, scan_vendor_mentions
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import CaseMethod, CaseObservable, PerCaseForensics
from shared.settings import Settings

NOW = datetime.now(UTC)


# ── 1. Mapping ↔ catalog contract ────────────────────────────────────────────


def _catalog_control_ids() -> tuple[set[str], set[str]]:
    itm = json.loads(Path("shared/data/itm_index.json").read_text(encoding="utf-8"))
    dts: set[str] = set()
    pvs: set[str] = set()
    for tech in itm["techniques"]:
        dts.update(d["id"] for d in tech.get("detections") or [])
        pvs.update(p["id"] for p in tech.get("preventions") or [])
    return dts, pvs


def test_mapping_validates_against_itm_catalog() -> None:
    """Every mapped DT/PV id exists in the served ITM catalog — an ITM
    refresh that renames or retires a control id fails here, not on the page."""
    catalog_dts, catalog_pvs = _catalog_control_ids()
    tooling = load_tooling_map()
    mapped_dts: list[str] = []
    mapped_pvs: list[str] = []
    ids = [c["id"] for c in tooling["categories"]]
    assert len(ids) == len(set(ids)), "duplicate category ids"
    for cat in tooling["categories"]:
        assert str(cat.get("rationale") or "").strip(), f"{cat['id']}: rationale required"
        assert cat.get("detections") or cat.get("preventions"), (
            f"{cat['id']}: maps no controls at all"
        )
        dangling_dt = set(cat["detections"]) - catalog_dts
        dangling_pv = set(cat["preventions"]) - catalog_pvs
        assert not dangling_dt, f"{cat['id']}: DT ids not in the ITM catalog: {dangling_dt}"
        assert not dangling_pv, f"{cat['id']}: PV ids not in the ITM catalog: {dangling_pv}"
        mapped_dts.extend(cat["detections"])
        mapped_pvs.extend(cat["preventions"])
    # Single-homing: a control id in two categories would double-count its
    # techniques' case volume across category scores.
    dup_dt = {k for k, n in Counter(mapped_dts).items() if n > 1}
    dup_pv = {k for k, n in Counter(mapped_pvs).items() if n > 1}
    assert not dup_dt, f"DT ids mapped to more than one category: {dup_dt}"
    assert not dup_pv, f"PV ids mapped to more than one category: {dup_pv}"


def test_every_category_has_two_to_six_vendor_examples() -> None:
    """Vendor examples contract: every category carries 2–6 distinct,
    non-empty example product names (display-only illustrations)."""
    for cat in load_tooling_map()["categories"]:
        ex = cat.get("examples")
        assert isinstance(ex, list), f"{cat['id']}: examples must be a list"
        assert 2 <= len(ex) <= 6, f"{cat['id']}: needs 2-6 examples, has {len(ex or [])}"
        assert all(isinstance(v, str) and v.strip() for v in ex), (
            f"{cat['id']}: examples must be non-empty strings"
        )
        assert len(set(ex)) == len(ex), f"{cat['id']}: duplicate examples"


# ── 2. Ranking math (synthetic, deterministic) ───────────────────────────────

_CATALOG = {
    # DT020 is deliberately a real crosswalk id (removable-media family) so
    # corroboration is exercised; DTX/PVX ids never hit the crosswalk.
    "T1": {"title": "USB exfil", "detections": ["DT020"], "preventions": ["PV900"]},
    "T2": {"title": "Cloud exfil", "detections": ["DTX2"], "preventions": []},
    "T3": {"title": "Unobserved", "detections": ["DT020"], "preventions": []},
}

_COUNTS = {
    "T1": {"cases": 6},
    "T2": {"cases": 4},
    "T9": {"cases": 5},  # not in the catalog → excluded from the volume base
    "T3": {"cases": 0},  # zero cases → not observed
}

_DETECTED_BY = [
    {"artifact": "removable-media (USB) logs", "cases": 3},
    {"artifact": "email logs / content", "cases": 2},
]


def test_ranking_math_synthetic() -> None:
    categories = [
        {"id": "a", "label": "Cat A", "detections": ["DT020"], "preventions": ["PV900"]},
        {"id": "b", "label": "Cat B", "detections": ["DTX2"], "preventions": []},
        {"id": "c", "label": "Cat C", "detections": [], "preventions": ["PVNONE"]},
    ]
    out = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY)
    assert out["observed_techniques"] == 2
    assert out["technique_case_volume"] == 10  # T1(6) + T2(4); T9/T3 excluded
    a, b, c = out["categories"]  # sorted: detect volume desc
    assert (a["id"], b["id"], c["id"]) == ("a", "b", "c")
    # Cat A: T1 both detected and prevented → 6 of 10 on both axes, "both".
    assert a["detect_volume"] == 6 and a["detection_coverage_pct"] == 60
    assert a["prevent_volume"] == 6 and a["prevention_coverage_pct"] == 60
    assert a["top_techniques"] == [{"id": "T1", "title": "USB exfil", "cases": 6, "covers": "both"}]
    # Full covered list == top list when the category reaches ≤ the head cap.
    assert a["covered_techniques"] == a["top_techniques"]
    # Corroborated via the USB record-class family (DT020 crosswalks there).
    assert a["corroborated_cases"] == 3
    assert a["corroborated_via"] == ["removable-media (USB) logs"]
    # Cat B: detection only, no crosswalk family names DTX2.
    assert b["detect_volume"] == 4 and b["detection_coverage_pct"] == 40
    assert b["prevent_volume"] == 0 and b["corroborated_cases"] == 0
    assert b["top_techniques"][0]["covers"] == "detect"
    # Cat C: maps nothing observed.
    assert c["detect_volume"] == 0 and c["prevent_volume"] == 0
    assert c["top_techniques"] == []


def test_ranking_small_n_suppresses_percentages_not_volumes() -> None:
    categories = [{"id": "a", "label": "A", "detections": ["DT020"], "preventions": []}]
    out = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY, suppress_pct=True)
    row = out["categories"][0]
    assert row["detection_coverage_pct"] is None
    assert row["prevention_coverage_pct"] is None
    assert row["detect_volume"] == 6  # counts always shown


def test_corroboration_takes_max_across_families_never_sum() -> None:
    """A category naming two record classes reports the MAX family count —
    a floor on distinct cases; summing would double-count cases that left
    evidence in both classes."""
    categories = [{"id": "x", "label": "X", "detections": ["DT020", "DT040"], "preventions": []}]
    out = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY)
    row = out["categories"][0]
    assert row["corroborated_cases"] == 3  # max(3, 2)
    assert row["corroborated_via"] == ["removable-media (USB) logs", "email logs / content"]


def test_covered_techniques_full_list_and_top_is_its_head() -> None:
    """`covered_techniques` carries EVERY observed technique the category's
    controls reach, ranked by case count (the category dossier's COVERS THESE
    OBSERVED TECHNIQUES section renders it whole); `top_techniques` is exactly
    its head slice — same list, same order, one computation."""
    catalog = {
        f"T{n}": {"title": f"Tech {n}", "detections": ["DT020"], "preventions": []}
        for n in range(8)
    }
    counts = {f"T{n}": {"cases": n + 1} for n in range(8)}
    categories = [{"id": "a", "label": "A", "detections": ["DT020"], "preventions": []}]
    row = rank_tool_categories(categories, counts, catalog, [])["categories"][0]
    assert [t["id"] for t in row["covered_techniques"]] == [f"T{n}" for n in range(7, -1, -1)]
    assert [t["cases"] for t in row["covered_techniques"]] == list(range(8, 0, -1))
    assert all(t["covers"] == "detect" for t in row["covered_techniques"])
    assert len(row["top_techniques"]) == 6
    assert row["top_techniques"] == row["covered_techniques"][:6]


def test_ranking_empty_corpus() -> None:
    out = rank_tool_categories(
        [{"id": "a", "label": "A", "detections": ["DT020"], "preventions": []}], {}, _CATALOG, []
    )
    assert out["technique_case_volume"] == 0
    assert out["categories"][0]["detection_coverage_pct"] is None


def test_examples_carried_verbatim_and_never_a_ranking_input() -> None:
    """Vendor examples are a display-only passthrough: each row carries its
    category's list verbatim, and stripping every examples array from the map
    leaves the ranking output byte-identical (regression against any future
    change that lets vendors influence scores or sort order)."""
    categories = [
        {
            "id": "a",
            "label": "Cat A",
            "detections": ["DT020"],
            "preventions": ["PV900"],
            "examples": ["Vendor One", "Vendor Two", "Vendor Three"],
        },
        {
            "id": "b",
            "label": "Cat B",
            "detections": ["DTX2"],
            "preventions": [],
            "examples": ["Vendor Four", "Vendor Five"],
        },
    ]
    out = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY)
    by_id = {c["id"]: c for c in out["categories"]}
    assert by_id["a"]["examples"] == ["Vendor One", "Vendor Two", "Vendor Three"]
    assert by_id["b"]["examples"] == ["Vendor Four", "Vendor Five"]

    stripped = copy.deepcopy(categories)
    for cat in stripped:
        del cat["examples"]
    out_stripped = rank_tool_categories(stripped, _COUNTS, _CATALOG, _DETECTED_BY)
    assert all(row["examples"] == [] for row in out_stripped["categories"])
    for result in (out, out_stripped):
        for row in result["categories"]:
            row.pop("examples")
    assert out == out_stripped, "stripping examples changed the ranking output"

    # Vendor MENTIONS are the same contract one layer up: the ranking core
    # never even sees the aliases file (no "vendors" key in its output), and
    # attaching mention counts — real scan or aliases-stripped empty scan —
    # changes NOTHING but the vendors block (category scores/sort identical).
    out_full = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY)
    assert all("vendors" not in row for row in out_full["categories"])
    vendor_cfg = [{"name": "Vendor One", "category": "a", "aliases": ["Vendor One"]}]
    scan = scan_vendor_mentions(
        [{"link": "https://ex.com/v1", "clean_text": "the Vendor One agent", "verdict_true": True}],
        vendor_cfg,
    )
    with_mentions = copy.deepcopy(out_full)
    attach_vendor_mentions(with_mentions["categories"], scan)
    without_aliases = copy.deepcopy(out_full)
    attach_vendor_mentions(without_aliases["categories"], {"mentions": {}})
    by_id = {c["id"]: c for c in with_mentions["categories"]}
    assert by_id["a"]["vendors"][0] == {
        "name": "Vendor One",
        "mentions_cases": {"verdict_true": 1, "total": 1},
        # Case receipts ride along (title falls back to the link when the
        # scan rows carry none; unpublished sorts as undated).
        "cases": [
            {
                "link": "https://ex.com/v1",
                "title": "https://ex.com/v1",
                "verdict_true": True,
                "published": None,
            }
        ],
        "more_cases": 0,
    }
    for result in (with_mentions, without_aliases):
        for row in result["categories"]:
            row.pop("vendors")
    assert with_mentions == without_aliases == out_full, (
        "the aliases file influenced category ranking output"
    )


# ── 3. GET /tooling — sweep-fresh endpoint over the in-memory index ──────────


def _forensics(link: str) -> PerCaseForensics:
    return PerCaseForensics(
        link=link,
        title=link,
        candidate_technique_ids=["IF002"],  # catalog: DT033/DT020/DT087 · PV016/PV003
        methods=[
            CaseMethod(
                action="USB copy of design files",
                claim_status="adjudicated",
                observables=[
                    CaseObservable(
                        description="mass copy to removable media",
                        artifact="EDR removable-media events",
                        channel="endpoint",
                        basis="mechanically_implied",
                    )
                ],
            )
        ],
        is_insider_case=True,
        confidence=0.9,
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    raws = [
        RawArticle(
            title=f"Case {n}",
            link=f"https://ex.com/case-{n}",
            summary="Departing employee used removable media.",
            content=(
                "Insider data exfiltration via USB drive by departing employee."
                # Case 0's stored text NAMES a vendor product (word-boundary,
                # court-prose style) so /tooling's mention counts are exercised.
                + (" The employer's CrowdStrike Falcon agent recorded the copy." if n == 0 else "")
            ),
            published=NOW,
            source_id="example",
            source_name="Example",
        )
        for n in range(2)
    ]
    processed = [
        p.model_copy(update={"forensics": _forensics(p.link)})
        for p in (process_article(raw) for raw in raws)
    ]
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(processed)

    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        SOCIAL_SUBSCRIPTIONS_PATH=str(tmp_path / "subs.json"),
        TECHNIQUE_HUNTS_PATH=str(tmp_path / "hunts.json"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)
    return TestClient(app)


def test_tooling_endpoint_ranks_against_verdict_true_cases(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/tooling")
        assert resp.status_code == 200
        data = resp.json()
        # Staleness stamp + basis: the same ledger fields the EVIDENCE page
        # cites, so the TOOLING basis line can say "against N verdict-true
        # cases as of DATE" without a second aggregation path.
        assert data["generated_at"]
        assert data["basis"]["contributing_cases"] == 2
        assert data["observed_techniques"] == 1  # IF002 only
        assert data["technique_case_volume"] == 2

        cats = {c["id"]: c for c in data["categories"]}
        # IF002 (DT020/DT087) → device-control detects the full observed volume
        # and the USB record-class family corroborates it in both cases.
        dc = cats["device-control"]
        assert dc["detect_volume"] == 2 and dc["prevent_volume"] == 0
        assert dc["corroborated_cases"] == 2
        assert dc["corroborated_via"] == ["removable-media (USB) logs"]
        assert dc["top_techniques"][0]["id"] == "IF002"
        assert dc["top_techniques"][0]["covers"] == "detect"
        # The category dossier's full covered list rides the same payload.
        assert dc["covered_techniques"] == dc["top_techniques"]
        # Control refs are spelled out for the page.
        assert {"id", "title"} <= set(dc["detections"][0])
        # Vendor examples thread through the payload verbatim from the map.
        map_examples = {c["id"]: c["examples"] for c in load_tooling_map()["categories"]}
        assert dc["examples"] == map_examples["device-control"]
        assert all(c["examples"] == map_examples[c["id"]] for c in data["categories"])
        # Mention-ranked vendors: every category carries its full examples set
        # with mentions_cases counts; case 0's text names CrowdStrike Falcon,
        # so it leads the edr row with 1 verdict-true of 1 total distinct case.
        assert all(
            {v["name"] for v in c["vendors"]} == set(map_examples[c["id"]])
            for c in data["categories"]
        )
        edr = cats["edr"]
        lead = edr["vendors"][0]
        assert lead["name"] == "CrowdStrike Falcon"
        assert lead["mentions_cases"] == {"verdict_true": 1, "total": 1}
        # Case receipts: the actual naming case rides the payload — link,
        # title, verdict flag, published stamp — for the vendor sheet.
        assert lead["more_cases"] == 0
        assert len(lead["cases"]) == 1
        ref = lead["cases"][0]
        assert ref["link"] == "https://ex.com/case-0"
        assert ref["title"] == "Case 0"
        assert ref["verdict_true"] is True
        assert ref["published"] and ref["published"].startswith(NOW.date().isoformat())
        assert all(
            v["mentions_cases"] == {"verdict_true": 0, "total": 0}
            and v["cases"] == []
            and v["more_cases"] == 0
            for v in edr["vendors"][1:]
        )
        # Unmentioned vendors trail in alphabetical order.
        assert [v["name"] for v in edr["vendors"][1:]] == sorted(
            (n for n in map_examples["edr"] if n != "CrowdStrike Falcon"), key=str.lower
        )
        # IF002 preventions (PV003/PV016) live in governance.
        assert cats["governance"]["prevent_volume"] == 2
        # Small-n law: 2 cases < floor → percentages suppressed, volumes shown.
        assert data["enriched_cases"] == 2 and data["enriched_cases"] < data["small_n_floor"]
        assert all(c["detection_coverage_pct"] is None for c in data["categories"])
        # Ranking: device-control outranks governance (detect volume first),
        # and the corroborated device-control row leads uncorroborated ties.
        order = [c["id"] for c in data["categories"]]
        assert order.index("device-control") < order.index("governance")
        assert order[0] == "device-control"


def test_tooling_endpoint_recomputes_per_reload(tmp_path, monkeypatch) -> None:
    """A sweep that rewrites the corpus re-ranks on the call after /reload —
    the payload is never a checked-in snapshot."""

    def _edr_lead(payload: dict) -> dict:
        return next(c for c in payload["categories"] if c["id"] == "edr")["vendors"][0]

    with _client(tmp_path, monkeypatch) as client:
        first = client.get("/tooling").json()
        assert first["technique_case_volume"] == 2
        assert _edr_lead(first)["mentions_cases"] == {"verdict_true": 1, "total": 1}
        assert _edr_lead(first)["cases"][0]["verdict_true"] is True
        # The "sweep": case 0 (the one whose text names CrowdStrike Falcon)
        # is re-adjudicated non-insider on disk.
        settings_path = service.get_settings().processed_articles_path
        store = JsonlProcessedStore(settings_path)
        rows = store.load_all()
        assert rows[0].link == "https://ex.com/case-0"
        rows[0] = rows[0].model_copy(
            update={"forensics": rows[0].forensics.model_copy(update={"is_insider_case": False})}
        )
        store.replace_all(rows)
        client.post("/reload")
        after = client.get("/tooling").json()
        assert after["technique_case_volume"] == 1
        # The mention scan was invalidated with the index swap: the document
        # still NAMES the product (total mention stands) but its verdict-true
        # mention is gone — same gate, recomputed, no stale cache. The case
        # receipt's verdict flag flips with it.
        lead = _edr_lead(after)
        assert lead["name"] == "CrowdStrike Falcon"
        assert lead["mentions_cases"] == {"verdict_true": 0, "total": 1}
        assert [(r["link"], r["verdict_true"]) for r in lead["cases"]] == [
            ("https://ex.com/case-0", False)
        ]


def test_mention_scan_runs_once_per_index_generation(tmp_path, monkeypatch) -> None:
    """The corpus scan is lazy and cached on the index object: repeated
    /tooling calls never rescan; /reload's index swap forces exactly one
    fresh scan on the next call (7k-doc per-request rescans are the failure
    mode this pins)."""
    calls = {"n": 0}
    real_scan = vendor_mentions_module.scan_vendor_mentions

    def counting_scan(rows, vendors):
        calls["n"] += 1
        return real_scan(rows, vendors)

    monkeypatch.setattr(vendor_mentions_module, "scan_vendor_mentions", counting_scan)
    with _client(tmp_path, monkeypatch) as client:
        client.get("/tooling")
        client.get("/tooling")
        client.get("/tooling")
        assert calls["n"] == 1, "per-request rescan — the index-generation cache is broken"
        client.post("/reload")
        client.get("/tooling")
        client.get("/tooling")
        assert calls["n"] == 2, "the /reload index swap must invalidate exactly once"


# ── 4. Web contract: TOOLING tab, pane, and api()-only data path ─────────────


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


def test_tooling_tab_pane_and_guide_line_present() -> None:
    html = _index_html()
    nav = re.search(r'<nav class="masthead-nav".*?</nav>', html, re.DOTALL)
    assert nav and 'data-pane="tooling"' in nav.group(0), "masthead TOOLING tab missing"
    mobile = re.search(r'<nav class="mobile-tabs".*?</nav>', html, re.DOTALL)
    assert mobile and 'data-pane="tooling"' in mobile.group(0), "mobile TOOLING tab missing"
    assert 'data-pane-panel="tooling"' in html, "TOOLING takeover pane missing"
    assert 'id="tlp-list"' in html and 'id="tlp-basis"' in html
    # Cheat-sheet line (test_site_guide enforces existence; pin the promise).
    cheat = re.search(r'id="guide-cheat".*?</dl>', html, re.DOTALL)
    assert cheat and "<dt>TOOLING</dt>" in cheat.group(0)
    assert "ranked by what caught real insiders" in cheat.group(0)
    # No vendor endorsements on the page shell.
    assert "Categories, never vendors" in html
    # Takeover CSS wired like EVIDENCE.
    css = Path("web/styles.css").read_text(encoding="utf-8")
    assert '.app-shell[data-pane="tooling"] .pane-tooling-page' in css


def test_tooling_path_reads_only_the_live_api() -> None:
    """renderToolingPage/loadToolingPage/openToolingView never fetch() outside
    the api() helper — the ranking must come from the live corpus (sweep +
    /reload propagates), never a checked-in static file."""
    src = _app_js()
    for name in ("tlpMeter", "renderToolingPage", "loadToolingPage", "openToolingView"):
        assert "fetch(" not in _fn_body(src, name), f"{name}() fetches outside api()"
    # The one live read: the session-cached ensureTooling (state.candidates
    # pattern) that loadToolingPage, the category dossiers, and the technique
    # dossiers' RELEVANT TOOLING join all share.
    assert 'api("/tooling"' in _fn_body(src, "ensureTooling")
    assert "ensureTooling(" in _fn_body(src, "loadToolingPage")
    # Techniques deep-link into the existing MATRIX dossier route.
    assert "selectTechnique(" in _fn_body(src, "renderToolingPage")
    # Basis line cites the ledger stamp, not a hardcoded date — one shared
    # builder so every TOOLING surface cites the same run.
    basis = _fn_body(src, "toolingBasisText")
    assert "VERDICT-TRUE CASES" in basis and "generated_at" in basis
    assert "renderToolingBasis(" in _fn_body(src, "renderToolingPage")
    # Route registered.
    assert '"/tooling"' in _fn_body(src, "parseRoute")


def test_examples_render_in_expanded_detail_never_in_collapsed_row() -> None:
    """Vendor lines appear inside the expanded category detail ONLY — the
    collapsed ranking row (everything renderToolingPage builds before the
    tlp-detail container) must never touch the examples string, the vendors
    block, or the mention-ranked line."""
    render = _fn_body(_app_js(), "renderToolingPage")
    marker = 'const detail = evpEl("div", "tlp-detail")'
    assert marker in render, "tlp-detail construction moved — update this contract test"
    collapsed, detail = render.split(marker, 1)
    assert "examples" not in collapsed, "vendor examples leaked into the collapsed ranking row"
    assert "vendors" not in collapsed, "the vendors block leaked into the collapsed ranking row"
    assert "mentions_cases" not in collapsed, "mention counts leaked into the collapsed row"
    assert "NAMED IN CASE RECORDS" not in collapsed
    assert "c.examples" in detail and "tlp-examples" in detail
    assert '"e.g. "' in detail or "`e.g. " in detail


def test_mention_ranked_vendor_line_only_in_expanded_detail() -> None:
    """The ranked vendor line renders in the expanded detail half only, with
    the operator's exact framing (presence in the record, not effectiveness):
    mentioned vendors as "Name ×N" chips with a verdict-true/total tooltip,
    unmentioned vendors trailing in the muted e.g. style."""
    render = _fn_body(_app_js(), "renderToolingPage")
    _, detail = render.split('const detail = evpEl("div", "tlp-detail")', 1)
    # Short verb-doctrine head; the presence-not-effectiveness framing rides
    # the tooltip (every-page-teaches-itself invariant).
    assert '"NAMED IN CASE RECORDS"' in detail
    assert "not an effectiveness score" in detail
    assert "c.vendors" in detail and "tlp-vendors" in detail
    # "Name ×N" chips, counts from mentions_cases, tooltip splits the counts.
    assert "×${m.total}" in detail
    assert "mentions_cases" in detail and "verdict_true" in detail
    assert "not effectiveness" in detail
    # Unmentioned vendors trail in the existing muted illustrative style.
    assert "tlp-examples" in detail


def test_single_basis_line_replaces_caveat_stack() -> None:
    """Operator call (2026-08-17): the TOOLING footer is ONE muted line —
    `BASED ON <N> VERDICT-TRUE CASES · AS OF <date>Z · METHODOLOGY · ITM™
    Forscie Ltd (not affiliated)` — with the old vendor-disclaimer and READ
    BEFORE BUYING paragraphs folded into the METHODOLOGY tooltip. Same
    rigor, new shape: the caveats must exist in the tooltip, never inline."""
    html = _index_html()
    # The removed inline blocks stay removed.
    assert "Vendor mention counts are corpus-derived receipts" not in html
    tooling_pane = re.search(
        r'<section class="pane pane-tooling-page".*?</section>', html, re.DOTALL
    )
    assert tooling_pane, "tooling pane not found"
    assert "READ BEFORE BUYING" not in tooling_pane.group(0)
    assert "tlp-vendor-note" not in tooling_pane.group(0)
    # One shared builder renders the line for every TOOLING surface.
    src = _app_js()
    basis = _fn_body(src, "renderToolingBasis")
    assert "BASED ON" in _fn_body(src, "toolingBasisText")
    assert '"METHODOLOGY"' in basis
    assert "ITM™ Forscie Ltd (not affiliated)" in basis
    # The compressed caveats live in the METHODOLOGY tooltip: coverage
    # meaning, CAUGHT/NAMED verb definitions, no-endorsement, litigated-case
    # bias pointer, authored-mapping + sweep-fresh recompute — plus the
    # observed-technique/volume counts the old long line carried.
    for phrase in (
        "not proof that control caught the insider",
        "CAUGHT ×N",
        "NAMED ×N",
        "never an endorsement",
        "vendors never affect category rankings",
        "EVIDENCE › LIMITATIONS",
        "recompute from the corpus every sweep",
        "observed techniques",
        "technique-case observations",
    ):
        assert phrase in basis, f"METHODOLOGY tooltip lost: {phrase!r}"
    # All four surfaces cite through the one builder.
    for fn in (
        "renderToolingPage",
        "renderToolingCategory",
        "renderToolsDirectory",
        "renderVendorSheet",
    ):
        assert "renderToolingBasis(" in _fn_body(src, fn), f"{fn}() bypasses the shared basis"


def test_live_refresh_busts_tooling_session_cache() -> None:
    """The LIVE refresh button re-primes the TOOLING pane after POST /reload —
    same contract as the matrix/evidence session caches. The cached /tooling
    payload itself must drop too: it also feeds the category dossiers and the
    technique dossiers' RELEVANT TOOLING join."""
    body = _fn_body(_app_js(), "refreshStream")
    assert "state.tooling = null" in body
    assert "toolingPageLoaded = false" in body
    assert "loadToolingPage(true)" in body
    # The NAMED TOOLS directory rides the same payload — its render cache
    # drops with the rest.
    assert "toolsDirectoryLoaded = false" in body
    assert "loadToolsDirectory(true)" in body


# ── 5. Matrix–tooling alignment: category dossier + dossier RELEVANT TOOLING ─
#
# The join between a technique's catalog controls and the category → control
# mapping runs CLIENT-SIDE (dossierToolingJoin in web/app.js) over two live
# session-cached reads — /itm and /tooling — so there is no second server
# aggregation path to drift. The function is pure by design; these unit tests
# execute its extracted source under node with synthetic fixtures (skipped
# when no node runtime exists — CI runners carry one).


def _node() -> str | None:
    for name in ("node", "node.exe", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_join(tech_dts: list, tech_pvs: list, categories: list) -> list:
    node = _node()
    if node is None:
        pytest.skip("no node runtime to execute the extracted join function")
    harness = (
        _fn_body(_app_js(), "dossierToolingJoin")
        + f"\nconst rows = dossierToolingJoin({json.dumps(tech_dts)}, "
        + f"{json.dumps(tech_pvs)}, {json.dumps(categories)});"
        + "\nprocess.stdout.write(JSON.stringify(rows));"
    )
    proc = subprocess.run([node, "-"], input=harness, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_join_overlap_computation_and_exclusion() -> None:
    """Categories join on DT/PV id intersection with THIS technique's catalog
    controls; a category intersecting neither axis is excluded entirely."""
    categories = [
        {"id": "hit", "label": "Hit", "detections": ["DT001", "DT099"], "preventions": ["PV001"]},
        {"id": "miss", "label": "Miss", "detections": ["DT098"], "preventions": ["PV098"]},
    ]
    rows = _run_join(["DT001", "DT002"], ["PV001"], categories)
    assert [r["category"]["id"] for r in rows] == ["hit"]
    assert rows[0]["dtOverlap"] == ["DT001"]  # only the intersecting id
    assert rows[0]["pvOverlap"] == ["PV001"]


def test_join_orders_by_detection_overlap_then_prevention_then_label() -> None:
    categories = [
        {"id": "pv-only", "label": "Alpha PV", "detections": [], "preventions": ["PV001"]},
        {"id": "one-dt", "label": "One DT", "detections": ["DT001"], "preventions": []},
        {"id": "two-dt", "label": "Two DT", "detections": ["DT001", "DT002"], "preventions": []},
        {
            "id": "one-dt-pv",
            "label": "One DT plus PV",
            "detections": ["DT002"],
            "preventions": ["PV001"],
        },
        {"id": "pv-only-b", "label": "Beta PV", "detections": [], "preventions": ["PV002"]},
    ]
    rows = _run_join(["DT001", "DT002"], ["PV001", "PV002"], categories)
    # Detections overlap first; prevention overlap breaks the tie; equal
    # overlaps fall back to the label so the order is deterministic.
    assert [r["category"]["id"] for r in rows] == [
        "two-dt",
        "one-dt-pv",
        "one-dt",
        "pv-only",
        "pv-only-b",
    ]


def test_join_labels_sides_and_accepts_both_id_shapes() -> None:
    """helps = detect | prevent | both — and the join accepts bare id strings
    (the tooling map) AND {id,...} control objects (the /itm catalog and the
    spelled-out /tooling payload), case-insensitively."""
    categories = [
        {"id": "b", "label": "B", "detections": [{"id": "dt001"}], "preventions": ["PV001"]},
        {"id": "d", "label": "D", "detections": ["DT002"], "preventions": []},
        {"id": "p", "label": "P", "detections": [], "preventions": [{"id": "PV002"}]},
    ]
    rows = _run_join(
        [{"id": "DT001", "title": "x"}, {"id": "DT002"}],
        [{"id": "PV001"}, {"id": "pv002"}],
        categories,
    )
    helps = {r["category"]["id"]: r["helps"] for r in rows}
    assert helps == {"b": "both", "d": "detect", "p": "prevent"}
    both = next(r for r in rows if r["category"]["id"] == "b")
    assert both["dtOverlap"] == ["DT001"] and both["pvOverlap"] == ["PV001"]


def test_category_dossier_route_and_back_navigation() -> None:
    """#/tooling/<category-id> routes to the category dossier; back must walk
    technique dossier → category dossier → ranked list through hash history."""
    src = _app_js()
    parse = _fn_body(src, "parseRoute")
    assert '"/tooling/"' in parse and "tooling-category" in parse
    apply_route = _fn_body(src, "applyRoute")
    assert "tooling-category" in apply_route and "openToolingCategoryView" in apply_route
    # Opening a category navigates (hash history entry), never renders in place.
    assert "navigate(`/tooling/" in _fn_body(src, "openToolingCategoryView")
    # Returning to #/tooling restores the ranked list inside the same pane.
    assert "showToolingList()" in _fn_body(src, "openToolingView")


def test_category_dossier_renders_from_api_data_only() -> None:
    """Every function on the category-dossier / dossier-tooling path reads the
    session-cached /tooling payload (api() only, never fetch()); the dossier
    carries verb labels, technique deep-links, the shared ledger citation, and
    teaching empty states."""
    src = _app_js()
    for name in (
        "ensureTooling",
        "showToolingList",
        "toolingBasisText",
        "tlcStat",
        "renderToolingCategory",
        "openToolingCategoryView",
        "openToolingCategory",
        "dossierToolingJoin",
        "renderDossierTooling",
        "loadDossierTooling",
    ):
        assert "fetch(" not in _fn_body(src, name), f"{name}() fetches outside api()"
    render = _fn_body(src, "renderToolingCategory")
    assert '"DETECTS"' in render and '"PREVENTS"' in render and '"CAUGHT"' in render
    assert "covered_techniques" in render
    assert "selectTechnique(" in render  # deep link to #/technique/<ID>
    assert "renderToolingBasis(" in render  # same citation the list renders
    assert "No observed technique yet" in render  # teaching empty state
    assert "No stored case document names a product" in render
    # Both-sides labeling is shared with the dossier section via one map.
    assert '"DETECTS + PREVENTS"' in src


def test_category_dossier_markup_and_purpose_lines() -> None:
    html = _index_html()
    for el_id in (
        "tlp-list-view",
        "tlc-view",
        "tlc-back",
        "tlc-title",
        "tlc-rationale",
        "tlc-stats",
        "tlc-vendors",
        "tlc-controls",
        "tlc-techs",
        "tlc-basis",
    ):
        assert f'id="{el_id}"' in html, f"#{el_id} missing from the tooling pane"
    # Section heads with methodology tooltips (teaches-itself doctrine).
    assert "NAMED IN CASE RECORDS" in html
    assert "IMPLEMENTS — ITM CONTROL ENTRIES" in html
    assert "COVERS THESE OBSERVED TECHNIQUES" in html
    # Purpose sub-line on the dossier itself.
    assert "One tool category against the observed case record" in html


def test_dossier_relevant_tooling_section_wired() -> None:
    """Every technique dossier renders RELEVANT TOOLING after its
    detections/preventions content: category chips with side labels, NAMED ×N
    vendor chips for corpus-mentioned vendors, muted e.g. for the rest."""
    html = _index_html()
    assert 'id="dossier-tooling"' in html and 'id="dossier-tooling-list"' in html
    assert "tool categories whose mapped ITM controls" in html
    # Section order: after the preventions list, before the case list.
    assert (
        html.index('id="dossier-prevention-list"')
        < html.index('id="dossier-tooling"')
        < html.index('id="dossier-article-list"')
    )
    src = _app_js()
    assert "loadDossierTooling(" in _fn_body(src, "showDossier")
    render = _fn_body(src, "renderDossierTooling")
    assert "dossierToolingJoin(" in render
    assert "openToolingCategory(" in render  # chip click-through to the dossier
    assert "NAMED ×" in render  # verb chips for documented case mentions
    assert "tlp-examples" in render  # unmentioned vendors keep the muted style
    assert "ensureTooling()" in _fn_body(src, "loadDossierTooling")


def test_tooling_list_row_opens_category_dossier() -> None:
    """The ranked list click-through: category names (collapsed row) and the
    FULL CATEGORY DOSSIER button (expanded detail) both route to the dossier."""
    render = _fn_body(_app_js(), "renderToolingPage")
    assert render.count("openToolingCategory(") >= 2
    collapsed, _detail = render.split('const detail = evpEl("div", "tlp-detail")', 1)
    assert "openToolingCategory(" in collapsed
