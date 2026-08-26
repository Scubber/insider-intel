"""EVIDENCE page DOM + client-logic contracts.

The page's markup and its client logic were essentially untested: ui_smoke_ci
only asserts that `.pane-evidence-page` becomes visible. These pin the parts a
refactor could silently break — above all that the deleted static findings file
cannot come back, and that the first click-to-sort table in the app sorts the
way the page's own structure requires.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


def _app_js() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def _index() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """Extract one top-level function by brace matching (test_tooling idiom)."""
    start = src.index(f"function {name}(")
    depth, i, seen = 0, src.index("{", start), False
    while i < len(src):
        if src[i] == "{":
            depth += 1
            seen = True
        elif src[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name}")


# ---------------------------------------------------------------------------
# The static findings file is gone and must stay gone
# ---------------------------------------------------------------------------


def test_static_findings_file_is_gone() -> None:
    """web/findings.json held hand-authored corpus numbers that froze on the
    day they were written — the exact bug the "no frozen numbers" invariant
    exists to prevent. A revert must not quietly reintroduce it."""
    assert not (WEB / "findings.json").exists()
    src = _app_js()
    assert "findings.json" not in src, "app.js still references the static findings file"
    assert "loadFindings" not in src, "the static findings fetch is back"


def test_findings_render_from_the_ledger_payload() -> None:
    src = _app_js()
    body = _fn_body(src, "renderFindings")
    assert "data.findings" in body and "data.finding_groups" in body
    assert "fetch(" not in body, "findings must ride the ledger payload, not a fetch"
    # The provenance tag says derived, not AI-authored: no model runs at read time.
    assert '"DERIVED"' in _fn_body(src, "evpFindingCard")
    assert "AI-ASSISTED" not in src


def test_findings_groups_use_the_shared_disclosure_idiom() -> None:
    """One expand gesture on the page, not two: the findings groups reuse the
    matrix .matrix-col summary/chevron CSS that the technique rows echo."""
    body = _fn_body(_app_js(), "renderFindings")
    assert "matrix-col" in body and "matrix-col-summary" in body
    assert "details" in body.lower()


def test_findings_header_no_longer_claims_publish_by_merge() -> None:
    html = _index()
    assert "operator-approved by merge" not in html
    assert "no stored numbers" in html


def test_legend_precedes_the_first_finding_card() -> None:
    """The colour key has to land before any number-bearing card, or the
    reader meets --accent and --signal with nothing to decode them."""
    html = _index()
    assert html.index('class="evp-legend"') < html.index('id="evp-findings"')


# ---------------------------------------------------------------------------
# buildEvidenceTechRows — the app's first click-to-sort table
# ---------------------------------------------------------------------------


def _node() -> str | None:
    for name in ("node", "node.exe", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_rows(techniques: list, themes: list, sort: str, direction: str = "desc") -> list:
    node = _node()
    if node is None:
        pytest.skip("no node runtime to execute the extracted builder")
    src = _app_js()
    order = re.search(r"const EVP_THEME_ORDER = \[.*?\];", src, re.DOTALL)
    assert order, "EVP_THEME_ORDER not found"
    harness = (
        order.group(0)
        + "\n"
        + _fn_body(src, "buildEvidenceTechRows")
        + f"\nconst rows = buildEvidenceTechRows({json.dumps(techniques)}, "
        + f"{json.dumps(themes)}, {json.dumps(sort)}, {json.dumps(direction)});"
        + "\nprocess.stdout.write(JSON.stringify(rows));"
    )
    proc = subprocess.run([node, "-"], input=harness, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _tech(tid, theme, cases, proven, alleged):
    return {
        "id": tid,
        "theme": theme,
        "cases": cases,
        "adjudicated_admitted": proven,
        "alleged": alleged,
    }


def test_sort_reorders_within_a_theme_never_across_it() -> None:
    """Theme grouping is the page's spine. A sort that hoisted a technique out
    of its stage would destroy the one thing the table is organised around."""
    techs = [
        _tech("IF001", "infringement", 10, 1, 9),
        _tech("IF002", "infringement", 90, 2, 88),
        _tech("ME001", "means", 50, 40, 10),
    ]
    themes = [{"theme": "means", "cases": 50, "adjudicated_admitted": 40}]
    rows = _run_rows(techs, themes, "cases")
    kinds = [r["kind"] for r in rows]
    assert kinds.count("theme") == 2
    seq = [r["tech"]["id"] if r["kind"] == "tech" else f"THEME:{r['theme']}" for r in rows]
    # means precedes infringement in EVP_THEME_ORDER regardless of counts.
    assert seq == ["THEME:means", "ME001", "THEME:infringement", "IF002", "IF001"]


def test_sort_key_switches_the_within_theme_order() -> None:
    techs = [
        _tech("IF001", "infringement", 10, 9, 1),
        _tech("IF002", "infringement", 90, 2, 88),
    ]
    by_cases = [r["tech"]["id"] for r in _run_rows(techs, [], "cases") if r["kind"] == "tech"]
    by_proven = [r["tech"]["id"] for r in _run_rows(techs, [], "proven") if r["kind"] == "tech"]
    by_alleged = [r["tech"]["id"] for r in _run_rows(techs, [], "alleged") if r["kind"] == "tech"]
    assert by_cases == ["IF002", "IF001"]
    assert by_proven == ["IF001", "IF002"], "PROVEN must rank by adjudicated alone"
    assert by_alleged == ["IF002", "IF001"]


def test_sort_direction_flips_and_ties_break_by_id() -> None:
    techs = [
        _tech("IF002", "infringement", 5, 1, 4),
        _tech("IF001", "infringement", 5, 1, 4),
    ]
    asc = [r["tech"]["id"] for r in _run_rows(techs, [], "cases", "asc") if r["kind"] == "tech"]
    desc = [r["tech"]["id"] for r in _run_rows(techs, [], "cases", "desc") if r["kind"] == "tech"]
    # Equal counts: the id tiebreak keeps the order stable in both directions.
    assert asc == ["IF001", "IF002"]
    assert desc == ["IF001", "IF002"]


def test_unknown_sort_key_falls_back_to_cases() -> None:
    techs = [_tech("IF001", "means", 3, 0, 3), _tech("IF002", "means", 7, 0, 7)]
    rows = _run_rows(techs, [], "not-a-column")
    assert [r["tech"]["id"] for r in rows if r["kind"] == "tech"] == ["IF002", "IF001"]


def test_builder_does_not_touch_the_dom() -> None:
    """It runs under bare node in these tests; a stray document reference
    would make it untestable and couple the sort to the renderer."""
    body = _fn_body(_app_js(), "buildEvidenceTechRows")
    assert "document" not in body and "getElementById" not in body


# ---------------------------------------------------------------------------
# Jurisdiction-aware dossier
# ---------------------------------------------------------------------------


def test_dossier_evidence_passes_the_jurisdiction_slice() -> None:
    """A sliced count reported as a global one is a silent lie."""
    body = _fn_body(_app_js(), "loadDossierEvidence")
    assert "evidenceCountry" in body
    assert "country: slice" in body or "{ country: slice }" in body
    assert "JURISDICTION" in body, "the panel must name the slice it is reporting"
    assert "404" in body, "absent-in-slice needs an explicit empty state"


# ---------------------------------------------------------------------------
# Trend surface
# ---------------------------------------------------------------------------


def test_trend_never_compares_into_the_partial_current_year() -> None:
    """CHANGE reads the last two COMPLETE years. Comparing into a year that is
    still filling manufactures a decline that never happened."""
    body = _fn_body(_app_js(), "renderEvidenceTrend")
    assert "const complete = shown.filter((y) => y !== current)" in body
    assert "generated_at" in body, "the current year must come from the ledger stamp"


def test_trend_suppresses_thin_years_and_hides_when_too_short() -> None:
    body = _fn_body(_app_js(), "renderEvidenceTrend")
    assert "small_n_floor" in body
    assert ">= floor" in body, "years under the reporting floor must be dropped"
    assert "years.length < 2" in body and "box.hidden = true" in body


def test_trend_is_a_count_matrix_not_a_line_chart() -> None:
    """A smooth curve would imply a measurement of insider behavior over time
    that a query-driven corpus cannot support. Counts, in cells, with the
    number present so the shading is never the only encoding."""
    body = _fn_body(_app_js(), "renderEvidenceTrend")
    assert "svg" not in body.lower() and "canvas" not in body.lower()
    # The count is written into the cell, not conveyed by color alone.
    assert 'n ? String(n) : "—"' in body
    # Sequential magnitude: one hue, light to dark.
    assert "var(--signal)" in body and "color-mix" in body


def test_trend_respects_the_colour_law() -> None:
    """--accent is reserved for court-proven. A rise in case VOLUME is observed
    signal and must not borrow the proven colour."""
    body = _fn_body(_app_js(), "renderEvidenceTrend")
    rise = body[body.index("const d = cell(later, id)") :][:400]
    assert "evp-algn" in rise, "a positive delta must wear --signal"
    assert "evp-adjn" not in rise, "a positive delta must not wear the court-proven accent"


def test_trend_table_scrolls_inside_its_own_container() -> None:
    """A wide year matrix must never make the page scroll sideways."""
    html = _index()
    trend = re.search(
        r'<div class="evp-region" id="evp-trend".*?\n            </div>', html, re.DOTALL
    )
    assert trend, "#evp-trend block not found"
    assert 'class="evp-scroll"' in trend.group(0)
    assert 'id="evp-trend-table"' in trend.group(0)
    # Teaches itself: section head, purpose line, methodology tooltip, note.
    assert "evp-section-head" in trend.group(0)
    assert "data-tip=" in trend.group(0)
    assert 'id="evp-trend-note"' in trend.group(0)


def test_trend_labels_the_axis_as_filing_year() -> None:
    """Never 'when it happened' — the corpus knows when a document was filed."""
    html = _index()
    assert "TECHNIQUES BY FILING YEAR" in html
    assert "not the year the incident happened" in html


# ---------------------------------------------------------------------------
# The memo layout (operator review, 2026-08-25)
#
# The findings were correct and still read like generated filler. These pin the
# structural half of the fix: a bottom line above, numbered findings, two card
# weights, the shared caveat once, and a technique rendered as the clickable
# control rather than as a bare ITM id in a sentence.
# ---------------------------------------------------------------------------


def test_bottom_line_lands_above_the_first_finding_group() -> None:
    """A memo opens with what it found, not with the first of five cards."""
    html = _index()
    assert html.index('id="evp-bottom-line"') < html.index('id="evp-findings-list"')


def test_shared_caveat_lands_once_below_the_findings() -> None:
    html = _index()
    assert html.count('id="evp-findings-caveat"') == 1
    assert html.index('id="evp-findings-list"') < html.index('id="evp-findings-caveat"')


def test_bottom_line_and_caveat_appear_only_with_findings() -> None:
    """Both are furniture for the findings; a thin slice shows neither."""
    body = _fn_body(_app_js(), "renderFindings")
    assert "data.bottom_line" in body
    assert "data.findings_caveat" in body
    assert "findings.length" in body


def test_findings_are_numbered_like_a_report() -> None:
    body = _fn_body(_app_js(), "evpFindingCard")
    assert "evp-finding-num" in body
    assert "`F${f.rank}`" in body


def test_a_supporting_finding_drops_the_full_card_anatomy() -> None:
    """Five cards at identical weight read as filler however good each one is.

    Recommendations still ship in the payload and still print in the CLI
    report — the page de-emphasises them, it does not lose them.
    """
    body = _fn_body(_app_js(), "evpFindingCard")
    assert 'f.weight !== "supporting"' in body
    assert "lead && (f.recommendations" in body
    assert "lead && f.method" in body


def test_a_technique_in_prose_renders_as_the_dossier_control() -> None:
    """Same control as the trend matrix and the technique table.

    A bare "MT003.002" in a sentence is unreadable and, worse, unclickable.
    The server writes a {technique} slot; the client fills it with the button.
    """
    src = _app_js()
    body = _fn_body(src, "evpFillProse")
    assert "evpTechniqueButton" in body
    assert "EVP_TECH_SLOT" in body
    assert 'const EVP_TECH_SLOT = "{technique}";' in src
    # A catalog miss must still produce words, never an empty hole.
    assert '"this technique"' in body


def test_finding_prose_never_renders_the_slot_as_text() -> None:
    card = _fn_body(_app_js(), "evpFindingCard")
    for field in ("f.title", "f.takeaway"):
        assert field in card
    # Every prose field goes through the filler, not straight into textContent.
    assert 'evpProse("evp-finding-takeaway", f.takeaway, ref)' in card
    assert 'evpFillProse(evpEl("span", "evp-finding-title"), f.title, ref)' in card


def test_the_inline_technique_button_flows_with_the_sentence() -> None:
    """.evp-tech-name is display:flex for the table; in prose it must be
    inline, or a finding's headline breaks onto its own line."""
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".evp-finding .evp-tech-name" in css
    block = css[css.index(".evp-finding .evp-tech-name") :][:220]
    assert "inline-flex" in block


def test_the_limitations_wall_stays_deleted() -> None:
    """Removed 2026-08-26 (operator decision).

    A wall of bolded caveats at the foot of the page is the same defect as a
    finding that states a caveat: nobody reads it, and its presence makes the
    page feel hedged rather than evidenced. Guarding it because a revert would
    otherwise restore it quietly, and dead CSS is how a deleted section comes
    back looking intentional.
    """
    html = _index()
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "evp-limits" not in html
    assert "evp-limits" not in css
    assert "LIMITATIONS" not in html
    assert "READ BEFORE CITING" not in html


def test_nothing_still_points_at_the_deleted_limitations_section() -> None:
    """TOOLING's methodology tooltip used to say "see EVIDENCE › LIMITATIONS".

    A cross-reference to a section that no longer exists is worse than no
    cross-reference: it sends the reader looking for a caveat they will never
    find.
    """
    src = _app_js()
    assert "EVIDENCE › LIMITATIONS" not in src
    # The litigated-case bias it pointed at is stated where the pointer was.
    assert "caught and litigated" in src


def test_the_itm_attribution_survived_the_deletion() -> None:
    """The trademark line rode inside the deleted block.

    It is an attribution, not a limitation, so it moves rather than goes: the
    EVIDENCE basis line now carries it, the same way TOOLING's does.
    """
    body = _fn_body(_app_js(), "renderEvidenceBasisLine")
    assert "Forscie" in body


def test_a_technique_the_catalog_cannot_name_is_marked_as_such() -> None:
    """A bare ITM id reading as if it were the technique's name is the defect
    the {technique} slot exists to prevent. When the catalog genuinely has no
    name there is nothing better to show — but the reader is told."""
    body = _fn_body(_app_js(), "evpTechniqueButton")
    assert "evp-tech-unnamed" in body
    assert "has no name for" in body
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".evp-tech-unnamed" in css


# ---------------------------------------------------------------------------
# Snapshot-first paint (2026-08-26)
#
# The page took ~75s to open and, when the probe ladder failed outright, never
# opened at all — measured at 90s+ with the API down. Two causes: EVIDENCE was
# missing from boot()'s pre-probe dispatch, and fetchLedger ignored
# data/ledger.json, a payload paintFromSnapshot already downloads on every
# load. These pin both, plus the one rule a cached paint must never break.
# ---------------------------------------------------------------------------


def test_evidence_opens_before_the_api_probe() -> None:
    """The single largest win: no reader waits out the cold-start ladder.

    boot() dispatches TOOLING and ABOUT before `await probeLiveApi()`;
    EVIDENCE was left out, so it sat behind the whole ladder — and when the
    ladder threw, the post-probe dispatch never ran at all.
    """
    src = _app_js()
    boot = src[src.index("const painted = await paintFromSnapshot();") :]
    early = boot[: boot.index("const health = await probeLiveApi();")]
    assert 'early.view === "evidence"' in early
    assert "openEvidenceView()" in early


def test_evidence_paints_from_the_snapshot_before_the_network() -> None:
    body = _fn_body(_app_js(), "fetchLedger")
    assert "fetchLedgerSnapshot()" in body
    # …and the snapshot read happens before the api() call in the same function.
    assert body.index("fetchLedgerSnapshot()") < body.index('api("/evidence/ledger"')


def test_the_snapshot_reader_fails_soft() -> None:
    """Absent or malformed file → null, and the caller goes live exactly as
    before. Same contract as fetchToolingSnapshot."""
    body = _fn_body(_app_js(), "fetchLedgerSnapshot")
    assert '"data/ledger.json"' in body
    assert "if (!res.ok) return null" in body
    assert "catch" in body


def test_only_the_global_view_is_served_from_the_snapshot() -> None:
    """The snapshot is one corpus-wide report.

    Serving a country tab from it would report a filtered number as a global
    one — the same class of lie the dossier ?country= fix removed.
    """
    body = _fn_body(_app_js(), "fetchLedger")
    snap = body[body.index("fetchLedgerSnapshot()") - 200 : body.index("fetchLedgerSnapshot()")]
    assert 'key === "ALL"' in snap


def test_a_cached_paint_never_claims_to_be_live() -> None:
    """The one rule the snapshot design must not break."""
    body = _fn_body(_app_js(), "renderEvidenceBasisLine")
    assert "evidenceLedgerIsLive" in body
    assert '"CACHED · "' in body


def test_the_live_refresh_is_single_flight_and_cannot_be_superseded() -> None:
    body = _fn_body(_app_js(), "refreshEvidenceLive")
    assert "evidenceLiveInflight" in body
    assert "!== inflight) return" in body, "a superseded response could overwrite live data"


def test_a_reload_retires_the_snapshot_for_the_session() -> None:
    """After a sweep the file on disk predates the corpus it would refresh for.

    Tracked separately from the CACHED stamp: reusing one flag for both let a
    /reload reset the stamp AND silently re-arm the stale snapshot.
    """
    src = _app_js()
    assert src.count("evidenceSnapshotAllowed = false") >= 3
    body = _fn_body(src, "fetchLedger")
    assert "evidenceSnapshotAllowed" in body


def test_the_live_swap_reuses_one_render_path() -> None:
    """The cold paint and the live re-render must not drift."""
    body = _fn_body(_app_js(), "rerenderEvidenceSurfaces")
    for renderer in (
        "renderEvidencePage",
        "renderFindings",
        "renderEvidenceTrend",
        "renderEvidenceBasisLine",
        "renderJurisdictionTabs",
    ):
        assert renderer in body
