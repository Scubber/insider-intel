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
