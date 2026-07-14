"""Reddit social ingestion: post mapping, pipeline dedupe, failure isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import apps.aggregator.reddit_pipeline as reddit_pipeline
from apps.aggregator.reddit import post_to_article, subreddit_source
from apps.aggregator.storage import JsonlArticleStore

FIXTURE = Path(__file__).parent / "fixtures" / "reddit_new_listing.json"


def _listing_posts() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [c["data"] for c in payload["data"]["children"] if c["kind"] == "t3"]


def test_post_to_article_mapping() -> None:
    post = _listing_posts()[0]
    fields = post_to_article(post, include_raw=True)
    assert fields is not None
    assert fields["source_id"] == "social-reddit-jobsearchhacks"
    assert fields["source_name"] == "Reddit r/jobsearchhacks"
    assert fields["channel"] == "social"
    assert fields["link"].startswith("https://www.reddit.com/r/jobsearchhacks/comments/")
    assert "overemployed" in fields["content"]
    assert "r/jobsearchhacks" in fields["summary"]
    assert "score=412" in fields["summary"]
    assert fields["published"] is not None
    assert fields["raw"]["author"] == "throwaway_oe"


def test_stickied_posts_are_skipped() -> None:
    stickied = _listing_posts()[1]
    assert stickied["stickied"] is True
    assert post_to_article(stickied) is None


def test_link_only_post_keeps_title_without_content() -> None:
    link_post = _listing_posts()[2]
    fields = post_to_article(link_post)
    assert fields is not None
    assert fields["content"] is None
    assert fields["summary"] == "(r/jobsearchhacks · score=55 · comments=12)"


def test_content_is_capped() -> None:
    post = dict(_listing_posts()[0])
    post["selftext"] = "x" * 5000
    fields = post_to_article(post, content_max_chars=100)
    assert fields is not None
    assert len(fields["content"]) == 100


def test_subreddit_source_normalizes() -> None:
    assert subreddit_source("r/OverEmployed") == (
        "social-reddit-overemployed",
        "Reddit r/overemployed",
    )


def test_pipeline_saves_and_dedupes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        reddit_pipeline,
        "fetch_subreddit_new",
        lambda sub, **kwargs: _listing_posts(),
    )
    store_path = tmp_path / "raw.jsonl"
    result = reddit_pipeline.run_reddit_ingestion(
        subreddits=["jobsearchhacks"],
        store_path=str(store_path),
        delay_seconds=0,
    )
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.success
    assert source.source_id == "social-reddit-jobsearchhacks"
    assert source.articles_fetched == 2  # stickied post dropped
    assert result.total_articles_saved == 2

    again = reddit_pipeline.run_reddit_ingestion(
        subreddits=["jobsearchhacks"],
        store_path=str(store_path),
        delay_seconds=0,
    )
    assert again.total_articles_saved == 0  # unchanged posts dedupe

    saved = JsonlArticleStore(store_path).load_all()
    assert {a.channel for a in saved} == {"social"}


def test_pipeline_isolates_per_subreddit_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(sub: str, **kwargs):
        if sub == "broken":
            raise RuntimeError("HTTP 429")
        return _listing_posts()

    monkeypatch.setattr(reddit_pipeline, "fetch_subreddit_new", fetch)
    result = reddit_pipeline.run_reddit_ingestion(
        subreddits=["broken", "jobsearchhacks"],
        store_path=str(tmp_path / "raw.jsonl"),
        delay_seconds=0,
    )
    by_id = {s.source_id: s for s in result.sources}
    assert not by_id["social-reddit-broken"].success
    assert "429" in (by_id["social-reddit-broken"].error or "")
    assert by_id["social-reddit-jobsearchhacks"].success
    assert result.total_articles_saved == 2


def test_no_subreddits_returns_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reddit_pipeline, "resolve_subreddits", lambda *a, **k: [])
    result = reddit_pipeline.run_reddit_ingestion(store_path=str(tmp_path / "raw.jsonl"))
    assert result.sources == []
    assert result.total_articles_saved == 0
