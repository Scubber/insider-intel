"""Contracts for scripts/recover_gutted_rows.py — the corpus-recover merge.

Pinned after the 2026-08-23 stale-generation overwrite: a writer pushed a
~2026-08-19-vintage corpus over the post-v3-sweep generation, deleting whole
rows and regressing enrichments. The merge must (a) restore gutted rows from
the donor, (b) RE-ADD rows only the donor still has (the original script
walked current rows only and silently dropped these), (c) union
enrichment_history on rows present in both sides without touching the kept
row's top-level projection, and (d) never downgrade a rich current row.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "recover_gutted_rows",
    Path(__file__).resolve().parents[1] / "scripts" / "recover_gutted_rows.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MOD)


def _hist(model: str, note: str, methods: int = 1, conf: float = 0.8) -> dict:
    return {
        "model": model,
        "schema_version": 3,
        "ai_summary": note,
        "forensics": {"methods": [{"action": f"m{i}"} for i in range(methods)], "confidence": conf},
    }


def _row(link: str, note: str = "", methods: int = 0, history: list[dict] | None = None) -> dict:
    return {
        "link": link,
        "title": f"case {link}",
        "ai_summary": note,
        "forensics": {"methods": [{"action": f"m{i}"} for i in range(methods)], "confidence": 0.7},
        "enrichment_history": history or [],
    }


def test_gutted_row_restored_from_richer_donor() -> None:
    current = [_row("a", note="", methods=0)]
    donor = [_row("a", note="full analyst note", methods=3)]
    merged, restored, readded, _ = _MOD.merge(current, donor)
    assert len(restored) == 1 and not readded
    assert merged[0]["ai_summary"] == "full analyst note"


def test_rich_current_row_never_downgraded() -> None:
    current = [_row("a", note="better re-enrichment", methods=5)]
    donor = [_row("a", note="older note", methods=2)]
    merged, restored, _, _ = _MOD.merge(current, donor)
    assert not restored
    assert merged[0]["ai_summary"] == "better re-enrichment"


def test_donor_only_rows_are_readded() -> None:
    """A stale-generation overwrite deletes whole rows; the union must bring
    them back with their enrichments intact."""
    current = [_row("kept", note="x", methods=1)]
    donor = [
        _row("kept", note="x", methods=1),
        _row(
            "lost-filing",
            note="sweep note",
            methods=4,
            history=[_hist("sparky", "sweep note", 4)],
        ),
    ]
    merged, _, readded, _ = _MOD.merge(current, donor)
    assert [r["link"] for r in readded] == ["lost-filing"]
    by_link = {r["link"]: r for r in merged}
    assert by_link["lost-filing"]["ai_summary"] == "sweep note"
    # Current-only rows always survive.
    assert "kept" in by_link


def test_history_union_preserves_both_generations_without_projection_change() -> None:
    cur_hist = _hist("old-model", "old note", 1, 0.5)
    donor_hist = _hist("sparky-v3", "sweep note", 4, 0.9)
    current = [_row("a", note="old note", methods=1, history=[cur_hist])]
    donor = [_row("a", note="sweep note", methods=4, history=[donor_hist, cur_hist])]
    merged, restored, _, history_merged = _MOD.merge(current, donor)
    # Current row is NOT gutted, so its projection stands (no restore) …
    assert not restored
    assert merged[0]["ai_summary"] == "old note"
    # … but the donor's sweep generation lands in history (append-only law),
    # deduped against the shared old generation.
    assert history_merged == 1
    sigs = [_MOD._history_signature(r) for r in merged[0]["enrichment_history"]]
    assert len(sigs) == len(set(sigs)) == 2


def test_identical_sides_report_nothing_to_recover() -> None:
    rows = [_row("a", note="x", methods=1, history=[_hist("m", "x")])]
    merged, restored, readded, history_merged = _MOD.merge(rows, [dict(r) for r in rows])
    assert not restored and not readded and history_merged == 0
    assert len(merged) == 1


def test_merge_does_not_mutate_input_rows() -> None:
    """The workflow dry-runs then re-runs on the same files; the first pass
    must not have quietly rewritten the inputs."""
    cur = _row("a", note="old note", methods=1, history=[_hist("old-model", "old note")])
    donor = _row("a", note="sweep", methods=4, history=[_hist("sparky-v3", "sweep", 4)])
    before = len(cur["enrichment_history"])
    _MOD.merge([cur], [donor])
    assert len(cur["enrichment_history"]) == before
