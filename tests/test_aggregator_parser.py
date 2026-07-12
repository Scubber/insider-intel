"""Unit tests for RSS parsing (no network)."""

from __future__ import annotations

import pytest

from apps.aggregator.parser import FeedParseError, parse_feed
from shared.schemas import FeedSource

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Threat Feed</title>
    <item>
      <title>Ransomware Campaign Observed</title>
      <link>https://example.com/articles/ransomware</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>Summary of the campaign.</description>
    </item>
    <item>
      <title>Missing Link Entry</title>
      <description>Should be skipped</description>
    </item>
    <item>
      <title>Zero-Day Disclosure</title>
      <link>https://example.com/articles/zeroday</link>
      <description>Details about a zero-day.</description>
    </item>
  </channel>
</rss>
"""

SOURCE = FeedSource(
    id="example",
    name="Example Feed",
    url="https://example.com/feed.xml",
)


def test_parse_feed_extracts_articles() -> None:
    articles = parse_feed(SAMPLE_RSS, SOURCE)

    assert len(articles) == 2
    assert articles[0].title == "Ransomware Campaign Observed"
    assert articles[0].link == "https://example.com/articles/ransomware"
    assert articles[0].summary == "Summary of the campaign."
    assert articles[0].source_id == "example"
    assert articles[0].published is not None
    assert articles[0].published.year == 2024


def test_parse_feed_include_raw() -> None:
    articles = parse_feed(SAMPLE_RSS, SOURCE, include_raw=True)
    assert articles[0].raw is not None
    assert articles[0].raw["title"] == "Ransomware Campaign Observed"


def test_parse_feed_unparseable_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed("not xml at all {{{", SOURCE)
