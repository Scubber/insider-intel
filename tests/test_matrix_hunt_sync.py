"""Scoped ITM catalog counts and curated overemployment aliases."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.search.index import ArticleSearchIndex
from shared.itm.aliases import CURATED_ALIASES
from shared.itm.index import load_itm_index
from shared.schemas import ProcessedArticle
from shared.schemas.articles import ExtractedEntities, ItmHit


def test_if038_has_overemployment_aliases() -> None:
    aliases = {a.lower() for a in CURATED_ALIASES["IF038"]}
    assert "overemployment" in aliases
    assert "moonlighting" in aliases
    tech = next(t for t in load_itm_index().techniques if t.id == "IF038")
    merged = {a.lower() for a in tech.aliases}
    assert "overemployment" in merged


def _hit(*, source_id: str, link: str) -> ProcessedArticle:
    return ProcessedArticle(
        title="Moonlighting case",
        link=link,
        published=datetime(2024, 1, 1, tzinfo=UTC),
        source_id=source_id,
        source_name=source_id,
        channel="news",  # type: ignore[arg-type]
        summary="Employee moonlighting without disclosure",
        clean_text="moonlighting",
        entities=ExtractedEntities(
            itm_hits=[
                ItmHit(
                    id="IF038",
                    title="Undisclosed Concurrent Employment",
                    theme="infringement",
                    article_id="AR4",
                    matched_aliases=["moonlighting"],
                )
            ],
            keywords_hit=["moonlighting"],
        ),
        relevance_score=0.5,
        itm_alignment="insider",
    )


def test_technique_article_counts_respect_source() -> None:
    index = ArticleSearchIndex(
        [
            _hit(source_id="krebs", link="https://example.com/a1"),
            _hit(source_id="darkreading", link="https://example.com/a2"),
        ]
    )
    assert index.technique_article_counts(topic_match=False).get("IF038", 0) == 2
    assert index.technique_article_counts(topic_match=False, source_id="krebs").get("IF038", 0) == 1
