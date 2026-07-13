"""Unit tests for JSONL article storage."""

from __future__ import annotations

from pathlib import Path

from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle


def _article(title: str, link: str) -> RawArticle:
    return RawArticle(
        title=title,
        link=link,
        summary="test",
        source_id="example",
        source_name="Example",
    )


def test_jsonl_store_saves_and_dedupes(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlArticleStore(path)

    first = store.save(
        [
            _article("A", "https://example.com/a"),
            _article("B", "https://example.com/b"),
        ]
    )
    assert first == 2

    second = store.save(
        [
            _article("A again", "https://example.com/a"),
            _article("C", "https://example.com/c"),
        ]
    )
    assert second == 1

    loaded = store.load_all()
    assert len(loaded) == 3
    links = {a.link for a in loaded}
    assert links == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }


def test_jsonl_store_reloads_known_links(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlArticleStore(path)
    store.save([_article("A", "https://example.com/a")])

    reopened = JsonlArticleStore(path)
    saved = reopened.save([_article("A", "https://example.com/a")])
    assert saved == 0


def test_refresh_saves_new_and_rewrites_changed(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlArticleStore(path)
    store.save([_article("A", "https://example.com/a")])
    original = store.load_all()[0]

    # Identical content → no-op.
    assert store.refresh([_article("A", "https://example.com/a")]) == (0, 0)

    # Changed summary → rewritten in place with a fresh ingested_at.
    updated = _article("A", "https://example.com/a")
    updated = updated.model_copy(update={"summary": "docket grew a new entry"})
    new, changed = store.refresh([updated, _article("B", "https://example.com/b")])
    assert (new, changed) == (1, 1)

    loaded = {a.link: a for a in store.load_all()}
    assert len(loaded) == 2
    row = loaded["https://example.com/a"]
    assert row.summary == "docket grew a new entry"
    assert row.ingested_at > original.ingested_at


def test_refresh_backfills_content_once(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlArticleStore(path)
    store.save([_article("A", "https://example.com/a")])

    enriched = _article("A", "https://example.com/a").model_copy(
        update={"content": "full opinion body"}
    )
    assert store.refresh([enriched]) == (0, 1)
    assert store.load_all()[0].content == "full opinion body"
    # Same enriched article again → fingerprint and content unchanged → no-op.
    assert store.refresh([enriched]) == (0, 0)


def test_refresh_index_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    JsonlArticleStore(path).save([_article("A", "https://example.com/a")])

    reopened = JsonlArticleStore(path)
    assert reopened.has_link("https://example.com/a")
    assert reopened.refresh([_article("A", "https://example.com/a")]) == (0, 0)
