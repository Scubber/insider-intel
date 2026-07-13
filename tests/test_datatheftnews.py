"""Tests for DataTheftNews Supabase ingest mapping."""

from __future__ import annotations

from apps.aggregator.datatheftnews import post_url, row_to_article
from apps.aggregator.datatheftnews_pipeline import run_datatheftnews_ingestion
from apps.aggregator.storage import JsonlArticleStore


def test_row_to_article_maps_slug_and_html() -> None:
    fields = row_to_article(
        {
            "title": "Trade Secret Case Study",
            "slug": "trade-secret-case-study",
            "excerpt": "Short blurb",
            "content": "<p>Full <b>body</b> text</p>",
            "category": "legal",
            "tags": ["insider", "trade-secret"],
            "published_at": "2026-01-15T12:00:00+00:00",
            "id": "abc",
        },
        include_raw=True,
    )
    assert fields is not None
    assert fields["title"] == "Trade Secret Case Study"
    assert fields["link"] == post_url("trade-secret-case-study")
    assert fields["source_id"] == "datatheftnews"
    assert fields["content"] == "Full body text"
    assert "Short blurb" in (fields["summary"] or "")
    assert fields["raw"]["slug"] == "trade-secret-case-study"


def test_row_to_article_skips_incomplete() -> None:
    assert row_to_article({"title": "x", "slug": ""}) is None
    assert row_to_article({"title": "", "slug": "x"}) is None


def test_run_datatheftnews_ingestion_uses_store(tmp_path, monkeypatch) -> None:
    rows = [
        {
            "title": "A",
            "slug": "a",
            "excerpt": "e",
            "content": "<p>hello</p>",
            "category": "insider-threats",
            "tags": [],
            "published_at": "2026-02-01T00:00:00Z",
            "id": "1",
        },
        {
            "title": "B",
            "slug": "b",
            "excerpt": None,
            "content": "<p>world</p>",
            "category": None,
            "tags": ["trade-secret"],
            "published_at": "2026-02-02T00:00:00Z",
            "id": "2",
        },
    ]

    monkeypatch.setattr(
        "apps.aggregator.datatheftnews_pipeline.resolve_anon_key",
        lambda _configured=None: "test-key",
    )
    monkeypatch.setattr(
        "apps.aggregator.datatheftnews_pipeline.fetch_published_posts",
        lambda **_kwargs: rows,
    )

    path = tmp_path / "articles.jsonl"
    result = run_datatheftnews_ingestion(store=JsonlArticleStore(path), limit=10)
    assert result.success_count == 1
    assert result.total_articles_saved == 2
    loaded = JsonlArticleStore(path).load_all()
    assert {a.link for a in loaded} == {
        "https://www.datatheftnews.com/blog/a",
        "https://www.datatheftnews.com/blog/b",
    }
