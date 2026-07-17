"""Story key fingerprint + stream clustering."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.search.cluster import cluster_hits, pick_primary
from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle, SearchHit
from shared.schemas.articles import ExtractedEntities
from shared.utils.story_key import compute_story_key, normalize_title


def test_normalize_title_strips_source_suffix() -> None:
    assert normalize_title("Apple sues OpenAI - Krebs on Security") == normalize_title(
        "Apple sues OpenAI | Dark Reading"
    )
    assert "krebs" not in normalize_title("Foo - Krebs on Security")


def test_compute_story_key_same_day_same_title() -> None:
    day = datetime(2024, 6, 1, 15, 0, tzinfo=UTC)
    a = compute_story_key("Insider stole secrets - Krebs", day)
    b = compute_story_key("Insider stole secrets | Dark Reading", day)
    assert a == b
    other_day = compute_story_key(
        "Insider stole secrets - Krebs",
        datetime(2024, 6, 2, tzinfo=UTC),
    )
    assert a != other_day


def _hit(
    *,
    title: str,
    source_id: str,
    channel: str = "news",
    score: float = 0.5,
    day: int = 1,
    story_key: str = "",
) -> SearchHit:
    published = datetime(2024, 6, day, 12, 0, tzinfo=UTC)
    key = story_key or compute_story_key(title, published)
    return SearchHit(
        title=title,
        link=f"https://example.com/{source_id}",
        source_id=source_id,
        source_name=source_id,
        channel=channel,
        published=published,
        summary="s",
        relevance_score=score,
        score=score,
        story_key=key,
    )


def test_cluster_same_channel_multi_source() -> None:
    clusters = cluster_hits(
        [
            _hit(title="Same Story", source_id="krebs", score=0.4),
            _hit(title="Same Story - Dark Reading", source_id="darkreading", score=0.9),
            _hit(title="Same Story", source_id="reddit-netsec", channel="tips", score=0.8),
        ]
    )
    # news cluster of 2 + tips cluster of 1
    assert len(clusters) == 2
    news = next(c for c in clusters if c.channel == "news")
    tips = next(c for c in clusters if c.channel == "tips")
    assert news.member_count == 2
    assert news.primary.source_id == "darkreading"
    assert len(news.siblings) == 1
    assert tips.member_count == 1


def test_pick_primary_prefers_non_reddit() -> None:
    members = [
        _hit(title="T", source_id="reddit-netsec", score=0.9),
        _hit(title="T", source_id="krebs", score=0.9),
    ]
    assert pick_primary(members).source_id == "krebs"


def test_cluster_handles_missing_published() -> None:
    """None / pre-epoch dates must not crash on Windows (timestamp OSError)."""
    bare = SearchHit(
        title="No date story",
        link="https://example.com/nodate",
        source_id="hrdive",
        source_name="HR Dive",
        channel="news",
        published=None,
        summary="s",
        relevance_score=0.5,
        score=0.5,
        story_key="nodate",
    )
    clusters = cluster_hits([bare, _hit(title="Other", source_id="krebs")])
    assert len(clusters) == 2


def test_list_articles_groups_by_default() -> None:
    day = datetime(2024, 6, 1, tzinfo=UTC)

    def article(source_id: str, score: float) -> ProcessedArticle:
        title = "Shared headline about exfil"
        return ProcessedArticle(
            title=title,
            link=f"https://example.com/{source_id}",
            published=day,
            source_id=source_id,
            source_name=source_id,
            channel="news",
            summary="s",
            clean_text="clean",
            entities=ExtractedEntities(),
            relevance_score=score,
            itm_alignment="insider",
            story_key=compute_story_key(title, day),
        )

    index = ArticleSearchIndex(
        [
            article("krebs", 0.4),
            article("darkreading", 0.7),
            article("bleeping", 0.5),
        ]
    )
    grouped = index.list_articles(limit=10, min_score=0, itm_alignment="all", group=True)
    assert grouped.count == 1
    assert len(grouped.clusters) == 1
    assert grouped.clusters[0].member_count == 3
    assert grouped.results[0].source_id == "darkreading"

    flat = index.list_articles(limit=10, min_score=0, itm_alignment="all", group=False)
    assert flat.count == 3
    assert flat.clusters == []


def test_parse_filing_reference() -> None:
    from shared.utils.story_key import parse_filing_reference

    summary = "Court: S.D.N.Y.\nDocket: 1:24-cr-00001\nCourtListener query: q"
    assert parse_filing_reference(summary) == ("S.D.N.Y.", "1:24-cr-00001")
    assert parse_filing_reference("Docket: 23-1234") == ("", "23-1234")
    assert parse_filing_reference("Court: S.D.N.Y.\nno docket here") is None
    assert parse_filing_reference(None) is None
    assert parse_filing_reference("Docket:   ") is None


def test_filing_story_key_stable_and_court_scoped() -> None:
    from shared.utils.story_key import filing_story_key

    key = filing_story_key("S.D.N.Y.", "1:24-cr-00001")
    assert key == filing_story_key("  s.d.n.y. ", "1:24-CR-00001")  # normalized
    assert key != filing_story_key("N.D. Cal.", "1:24-cr-00001")  # court-scoped
    assert key != filing_story_key("S.D.N.Y.", "1:24-cr-00002")
