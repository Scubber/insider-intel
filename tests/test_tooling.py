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
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from apps.search.tooling import load_tooling_map, rank_tool_categories
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
    assert a["top_techniques"] == [
        {"id": "T1", "title": "USB exfil", "cases": 6, "covers": "both"}
    ]
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
    categories = [
        {"id": "x", "label": "X", "detections": ["DT020", "DT040"], "preventions": []}
    ]
    out = rank_tool_categories(categories, _COUNTS, _CATALOG, _DETECTED_BY)
    row = out["categories"][0]
    assert row["corroborated_cases"] == 3  # max(3, 2)
    assert row["corroborated_via"] == ["removable-media (USB) logs", "email logs / content"]


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
            content="Insider data exfiltration via USB drive by departing employee.",
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
        # Control refs are spelled out for the page.
        assert {"id", "title"} <= set(dc["detections"][0])
        # Vendor examples thread through the payload verbatim from the map.
        map_examples = {c["id"]: c["examples"] for c in load_tooling_map()["categories"]}
        assert dc["examples"] == map_examples["device-control"]
        assert all(c["examples"] == map_examples[c["id"]] for c in data["categories"])
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
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/tooling").json()["technique_case_volume"] == 2
        # The "sweep": one case is re-adjudicated non-insider on disk.
        settings_path = service.get_settings().processed_articles_path
        store = JsonlProcessedStore(settings_path)
        rows = store.load_all()
        rows[0] = rows[0].model_copy(
            update={"forensics": rows[0].forensics.model_copy(update={"is_insider_case": False})}
        )
        store.replace_all(rows)
        client.post("/reload")
        assert client.get("/tooling").json()["technique_case_volume"] == 1


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
    assert 'api("/tooling"' in _fn_body(src, "loadToolingPage")
    # Techniques deep-link into the existing MATRIX dossier route.
    assert "selectTechnique(" in _fn_body(src, "renderToolingPage")
    # Basis line cites the ledger stamp, not a hardcoded date.
    render = _fn_body(src, "renderToolingPage")
    assert "VERDICT-TRUE CASES" in render and "generated_at" in render
    # Route registered.
    assert '"/tooling"' in _fn_body(src, "parseRoute")


def test_examples_render_in_expanded_detail_never_in_collapsed_row() -> None:
    """Vendor examples appear inside the expanded category detail ONLY — the
    collapsed ranking row (everything renderToolingPage builds before the
    tlp-detail container) must never touch the examples string."""
    render = _fn_body(_app_js(), "renderToolingPage")
    marker = 'const detail = evpEl("div", "tlp-detail")'
    assert marker in render, "tlp-detail construction moved — update this contract test"
    collapsed, detail = render.split(marker, 1)
    assert "examples" not in collapsed, "vendor examples leaked into the collapsed ranking row"
    assert "c.examples" in detail and "tlp-examples" in detail
    assert '"e.g. "' in detail or "`e.g. " in detail


def test_vendor_disclaimer_rendered_once_near_basis_line() -> None:
    """The vendor disclaimer ships once, muted (tlp-basis treatment),
    directly after the ledger basis line."""
    html = _index_html()
    disclaimer = "Examples are common products in each category, not endorsements and not derived"
    assert html.count(disclaimer) == 1
    assert "Rankings never consider vendors." in html
    assert re.search(
        r'id="tlp-basis" hidden></p>\s*<p class="tlp-basis tlp-vendor-note">',
        html,
    ), "vendor disclaimer must sit next to the basis line with the muted treatment"


def test_live_refresh_busts_tooling_session_cache() -> None:
    """The LIVE refresh button re-primes the TOOLING pane after POST /reload —
    same contract as the matrix/evidence session caches."""
    body = _fn_body(_app_js(), "refreshStream")
    assert "toolingPageLoaded = false" in body
    assert "loadToolingPage(true)" in body
