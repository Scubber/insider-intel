"""MATRIX tab + technique dossiers: every corpus-derived piece is refresh-fed.

Operator contract (2026-08-16): "dossier pages are dynamically updated …
any time we sweep." The matrix browse view and the technique dossiers must
read ONLY from (a) live API endpoints computed from the loaded corpus index
(reloaded by the refresh job's POST /reload) or (b) refresh-job-written state
files re-read from disk on every call — never from checked-in files that go
stale after a corpus sweep.

The one deliberately corpus-independent piece is the ITM catalog shell
(technique titles / themes / descriptions / aliases and the DT*/PV* control
catalog) from shared/data/itm_index.json: that mirrors the upstream Insider
Threat Matrix and is refreshed by its own workflow
(.github/workflows/itm-refresh.yml), not by corpus sweeps. Everything the
sweep changes — per-technique article counts, the dossier case list, observed
evidence, synthesized hunts, novel candidates — must flow from the corpus.

Client checks mirror tests/test_boot_snapshot.py's mechanical style: regex the
shipped web/app.js so a static-file read creeping into the matrix path fails
CI, not a code review.
"""

from __future__ import annotations

import re
from pathlib import Path

# Every function on the matrix-tab / dossier render-and-load path. A new
# helper on this path should be added here so its data sources stay pinned.
_MATRIX_PATH_FUNCTIONS = (
    "ensureItmCatalog",
    "ensureCandidates",
    "openMatrixView",
    "renderMatrixBrowse",
    "renderMatrixColumns",
    "renderMatrixControlList",
    "renderCandidates",
    "showDossier",
    "renderDossierShell",
    "renderDossierArticles",
    "renderDossierHunts",
    "loadDossierEvidence",
    "dossierToolingJoin",
    "renderDossierTooling",
    "loadDossierTooling",
    "selectDetection",
    "selectPrevention",
)


def _app_js() -> str:
    return Path("web/app.js").read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """Extract a top-level (2-space-indented) function body from web/app.js."""
    match = re.search(
        rf"\n  (?:async )?function {re.escape(name)}\(.*?\n  \}}",
        source,
        re.DOTALL,
    )
    assert match, f"{name}() not found in web/app.js — update _MATRIX_PATH_FUNCTIONS"
    return match.group(0)


def test_matrix_and_dossier_read_only_live_api_sources() -> None:
    """The matrix path calls api() (live corpus endpoints) and never fetch()es
    a checked-in static file — so a sweep + /reload propagates on the next
    click with no redeploy."""
    src = _app_js()
    for name in _MATRIX_PATH_FUNCTIONS:
        body = _fn_body(src, name)
        assert "fetch(" not in body, (
            f"{name}() fetches outside the api() helper — matrix/dossier data "
            "must come from the live API (or refresh-written state served by "
            "it), never a checked-in static file that a sweep cannot update"
        )

    # The corpus-derived sources, pinned by endpoint:
    assert 'api("/itm"' in _fn_body(src, "ensureItmCatalog")  # per-technique counts
    assert 'api("/techniques/candidates")' in _fn_body(src, "ensureCandidates")
    dossier = _fn_body(src, "showDossier")
    assert 'api("/articles"' in dossier and "itm_id: tech.id" in dossier
    assert "/evidence/technique/" in _fn_body(src, "loadDossierEvidence")
    # RELEVANT TOOLING joins the session-cached live /tooling payload — the
    # same ensureTooling read the TOOLING page uses (pinned in
    # tests/test_tooling.py), never a checked-in mapping snapshot.
    assert "loadDossierTooling(" in dossier
    assert "ensureTooling()" in _fn_body(src, "loadDossierTooling")
    assert 'api("/tooling"' in _fn_body(src, "ensureTooling")
    assert 'api("/articles"' in _fn_body(src, "selectDetection")
    assert 'api("/articles"' in _fn_body(src, "selectPrevention")


def test_live_refresh_busts_matrix_session_caches() -> None:
    """The LIVE refresh button re-primes every matrix-tab session cache after
    POST /reload: catalog (counts) AND the novel-candidate view. The candidates
    reset regressed once — the CANDIDATES tab kept pre-sweep data until a full
    page reload."""
    body = _fn_body(_app_js(), "refreshStream")
    assert 'api("/reload"' in body
    assert "state.itmCatalog = null" in body
    assert "state.candidates = null" in body
    assert "ensureItmCatalog(true)" in body


def test_candidate_view_reads_swept_state_fresh_per_call(tmp_path, monkeypatch) -> None:
    """/techniques/candidates re-reads the job-written technique_seeds.json on
    every call — a corpus sweep's rewrite is visible on the next API hit with
    no process restart and no /reload dependency."""
    from apps.aggregator.technique_seeds import TechniqueSeedStore
    from apps.search import service
    from shared.schemas.discovery import CandidateCatalogResponse

    seeds_path = tmp_path / "technique_seeds.json"
    monkeypatch.setenv("TECHNIQUE_SEEDS_PATH", str(seeds_path))

    store = TechniqueSeedStore(seeds_path)
    store.write(CandidateCatalogResponse(candidate_count=1, counts_by_status={"seed": 1}))
    assert service.candidate_catalog().candidate_count == 1

    # The sweep rewrites the view; the very next call serves it.
    store.write(CandidateCatalogResponse(candidate_count=2, counts_by_status={"seed": 2}))
    assert service.candidate_catalog().candidate_count == 2


def test_dossier_hunts_read_swept_state_fresh_per_call(tmp_path, monkeypatch) -> None:
    """The dossier's synthesized hunt patterns re-read the job-written
    technique_hunts.json per call — same sweep-propagation contract as the
    candidate view."""
    from apps.aggregator.hunt_synthesis import TechniqueHuntStore
    from apps.search.service import _synthesized_hunts
    from shared.schemas.hunt_patterns import HuntPattern, TechniqueHuntEntry

    hunts_path = tmp_path / "technique_hunts.json"
    monkeypatch.setenv("TECHNIQUE_HUNTS_PATH", str(hunts_path))

    def entry(pattern_name: str) -> TechniqueHuntEntry:
        return TechniqueHuntEntry(
            technique_id="IF038",
            signature="sig",
            patterns=[HuntPattern(name=pattern_name, detect=["watch for it"])],
        )

    store = TechniqueHuntStore(hunts_path)
    store.write({"IF038": entry("first generation")})
    assert _synthesized_hunts("IF038")["patterns"][0]["name"] == "first generation"

    store.write({"IF038": entry("post-sweep generation")})
    assert _synthesized_hunts("IF038")["patterns"][0]["name"] == "post-sweep generation"


def test_untyped_insider_stamp_reads_only_snapshot_kept_fields() -> None:
    """The card's three-way stamp (context / insider-type / unclassified) keys
    off fields that survive the boot-snapshot slimming, so CACHED and LIVE
    render the same verdicts. is_insider_case is the gate for both the context
    stamp and the unclassified stamp."""
    from scripts.export_boot_snapshot import _KEEP_FORENSICS_KEYS

    src = _app_js()
    row_builder = _fn_body(src, "buildArticleRow")
    assert "insider-type-unclassified" in row_builder
    assert "is_insider_case" in _KEEP_FORENSICS_KEYS
