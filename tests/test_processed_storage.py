"""Regression tests for the processed store's append-only upsert contract.

The 2026-08-16 staging proof run lost 10 of 14 enrichments because a later
pipeline stage's whole-file rewrite carried stale copies of rows an earlier
stage had already updated. These tests pin the fix: mid-cycle writes append
(a writer can only affect links it explicitly writes), the reader is
last-line-wins, and only compact() rewrites the file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.schemas import ExtractedEntities, ProcessedArticle


def _article(link_slug: str, *, ai_summary: str | None = None) -> ProcessedArticle:
    return ProcessedArticle(
        title=link_slug,
        link=f"https://example.com/{link_slug}",
        published=datetime(2024, 1, 1, tzinfo=UTC),
        source_id="example",
        source_name="Example",
        channel="filings",  # type: ignore[arg-type]
        summary="summary",
        clean_text="clean",
        entities=ExtractedEntities(),
        relevance_score=0.5,
        itm_alignment="insider",
        ai_summary=ai_summary,
    )


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_upsert_appends_instead_of_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlProcessedStore(path)
    store.save([_article("a"), _article("b")])

    store.upsert([_article("a", ai_summary="enriched")])

    # Append-only: the file grew by exactly the upserted row; the original
    # line for "a" is still physically present above it.
    assert len(_lines(path)) == 3
    rows = {a.link: a for a in store.load_all()}
    assert len(rows) == 2
    assert rows["https://example.com/a"].ai_summary == "enriched"


def test_stale_copy_writer_cannot_erase_another_stages_update(tmp_path: Path) -> None:
    """The 2026-08-16 clobber, replayed against the new contract."""
    path = tmp_path / "articles.jsonl"
    store = JsonlProcessedStore(path)
    store.save([_article("a"), _article("b")])

    # Stage 2 captured its working copies BEFORE stage 1 wrote its update —
    # the exact stale-view situation from the incident.
    stale_copies = store.load_all()
    stale_b = next(a for a in stale_copies if a.link.endswith("/b"))

    store.upsert([_article("a", ai_summary="stage-1 enrichment")])  # stage 1
    store.upsert([stale_b])  # stage 2 writes only what it touched

    rows = {a.link: a for a in store.load_all()}
    assert rows["https://example.com/a"].ai_summary == "stage-1 enrichment"


def test_compact_folds_duplicates_and_keeps_latest(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlProcessedStore(path)
    store.save([_article("a"), _article("b")])
    store.upsert([_article("a", ai_summary="v1")])
    store.upsert([_article("a", ai_summary="v2")])
    assert len(_lines(path)) == 4

    unique = store.compact()

    assert unique == 2
    assert len(_lines(path)) == 2
    rows = {a.link: a for a in store.load_all()}
    assert rows["https://example.com/a"].ai_summary == "v2"


def test_torn_final_line_is_tolerated(tmp_path: Path) -> None:
    """A kill mid-append leaves at worst one torn line; readers skip it."""
    path = tmp_path / "articles.jsonl"
    store = JsonlProcessedStore(path)
    store.save([_article("a")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"title": "torn')  # simulated kill mid-write

    fresh = JsonlProcessedStore(path)
    assert [a.link for a in fresh.load_all()] == ["https://example.com/a"]
    fresh.upsert([_article("a", ai_summary="post-crash")])
    rows = fresh.load_all()
    assert rows[0].ai_summary == "post-crash"
