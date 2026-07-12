"""Cluster filtered SearchHits into story cards (within channel)."""

from __future__ import annotations

from datetime import datetime

from shared.schemas.search import SearchHit, StoryCluster
from shared.utils.story_key import (
    cluster_bucket_key,
    compute_story_key,
)


def ensure_story_key(hit: SearchHit) -> str:
    """Use stored key or compute on the fly for legacy rows."""
    key = (hit.story_key or "").strip()
    if key:
        return key
    return compute_story_key(hit.title, hit.published)


def _is_redditish(source_id: str) -> bool:
    sid = (source_id or "").lower()
    return sid.startswith(("reddit-", "tip-"))


def _safe_timestamp(pub: datetime | None) -> float:
    """Unix ts for sorting; Windows raises OSError on datetime.min / pre-1970."""
    if pub is None:
        return 0.0
    try:
        return pub.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def _primary_sort_key(hit: SearchHit) -> tuple:
    # Prefer higher relevance, newer date, non-reddit sources
    return (
        hit.relevance_score,
        _safe_timestamp(hit.published),
        0 if _is_redditish(hit.source_id) else 1,
    )


def pick_primary(members: list[SearchHit]) -> SearchHit:
    return max(members, key=_primary_sort_key)


def cluster_hits(hits: list[SearchHit]) -> list[StoryCluster]:
    """Group hits by channel+story_key; order clusters by primary published desc."""
    buckets: dict[str, list[SearchHit]] = {}
    for hit in hits:
        key = ensure_story_key(hit)
        channel = (hit.channel or "news").strip().lower() or "news"
        # Mutate a copy so response carries story_key even for legacy
        if not hit.story_key:
            hit.story_key = key
        bucket = cluster_bucket_key(key, channel)
        buckets.setdefault(bucket, []).append(hit)

    clusters: list[StoryCluster] = []
    for members in buckets.values():
        primary = pick_primary(members)
        siblings = [m for m in members if m.link != primary.link]
        # Stable sibling order: name then link
        siblings.sort(key=lambda h: (h.source_name.lower(), h.link))
        clusters.append(
            StoryCluster(
                story_key=ensure_story_key(primary),
                channel=(primary.channel or "news"),
                primary=primary,
                siblings=siblings,
                member_count=len(members),
            )
        )

    def _cluster_sort(c: StoryCluster) -> tuple:
        return (_safe_timestamp(c.primary.published), c.primary.relevance_score)

    clusters.sort(key=_cluster_sort, reverse=True)
    return clusters
