"""Sitemap parse + HTML extract unit tests for archive ingest."""

from __future__ import annotations

from apps.aggregator.html_extract import extract_article_html, text_matches_keywords
from apps.aggregator.sitemap import parse_sitemap_xml, url_matches_keywords


def test_parse_sitemap_index_and_urlset() -> None:
    index = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/news-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
    </sitemapindex>
    """
    pages, children = parse_sitemap_xml(index)
    assert pages == []
    assert children == [
        "https://example.com/news-sitemap.xml",
        "https://example.com/post-sitemap.xml",
    ]

    urlset = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/news/moonlighting-policy/</loc></url>
      <url><loc>https://example.com/news/benefits-guide/</loc></url>
    </urlset>
    """
    pages2, children2 = parse_sitemap_xml(urlset)
    assert children2 == []
    assert pages2 == [
        "https://example.com/news/moonlighting-policy/",
        "https://example.com/news/benefits-guide/",
    ]


def test_url_keyword_filter() -> None:
    assert url_matches_keywords(
        "https://example.com/news/moonlighting-policy/",
        ["moonlighting", "overemployment"],
    )
    assert not url_matches_keywords(
        "https://example.com/news/benefits-guide/",
        ["moonlighting", "overemployment"],
    )


def test_extract_article_html_meta() -> None:
    html = """<!DOCTYPE html><html><head>
    <title>Fallback Title</title>
    <meta property="og:title" content="Moonlighting and conflict of interest" />
    <meta property="og:description" content="Employers tighten outside employment rules." />
    <meta property="article:published_time" content="2024-03-15T12:00:00Z" />
    </head><body><p>Body</p></body></html>"""
    art = extract_article_html(html)
    assert art.title == "Moonlighting and conflict of interest"
    assert art.summary and "outside employment" in art.summary.lower()
    assert art.published is not None
    assert art.published.year == 2024
    assert text_matches_keywords(art.title, ["moonlighting"])
