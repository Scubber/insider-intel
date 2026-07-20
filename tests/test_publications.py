"""Publications lane: extraction, catalog sweep, channel, and score gate."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.publication_extract import (
    PublicationFetchError,
    extract_page_text,
    fetch_publication,
    find_pdf_links,
)
from apps.aggregator.publication_sources import (
    PublicationSource,
    get_publication_sources,
)
from apps.aggregator.publications_pipeline import (
    ingest_publication_url,
    run_publications_ingestion,
)
from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from shared.schemas.articles import resolve_channel

FIXTURES = Path(__file__).parent / "fixtures"
LANDING_HTML = (FIXTURES / "publication_landing.html").read_text(encoding="utf-8")
SAMPLE_PDF = (FIXTURES / "publication_sample.pdf").read_bytes()
PDF_TEXT = "Insider threat mitigation guidance for security teams."

LANDING_URL = "https://pubs.example.org/library/common-sense-guide/"
PDF_URL = "https://pubs.example.org/library/uploads/common-sense-guide-7e.pdf"


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _serve(url_map):
    def handler(request: httpx.Request) -> httpx.Response:
        entry = url_map.get(str(request.url).split("?")[0])
        if entry is None:
            return httpx.Response(404)
        status, content, ctype = entry
        return httpx.Response(status, content=content, headers={"content-type": ctype})

    return handler


def _fetch(url, client, **overrides):
    kwargs = {
        "user_agent": "test-agent",
        "content_max_chars": 150_000,
        "pdf_max_bytes": 25_000_000,
    }
    kwargs.update(overrides)
    return fetch_publication(url, client=client, **kwargs)


def test_find_pdf_links_dedupes_and_resolves() -> None:
    links = find_pdf_links(LANDING_HTML, LANDING_URL)
    assert links == [
        "https://pubs.example.org/library/uploads/common-sense-guide-7e.pdf?download=1",
        PDF_URL,
    ]


def test_extract_page_text_skips_script_and_style() -> None:
    text = extract_page_text(LANDING_HTML)
    assert "insider threat mitigation programs" in text
    assert "should not appear" not in text
    assert "display: none" not in text


def test_fetch_publication_prefers_pdf_text() -> None:
    url_map = {
        LANDING_URL: (200, LANDING_HTML.encode(), "text/html"),
        PDF_URL: (200, SAMPLE_PDF, "application/pdf"),
    }
    with _mock_client(_serve(url_map)) as client:
        doc = _fetch(LANDING_URL, client)
    assert doc.title == "Common Sense Guide to Mitigating Insider Threats, Seventh Edition"
    assert doc.summary and "Best practices" in doc.summary
    assert doc.published is not None
    assert doc.content == PDF_TEXT
    assert doc.pdf_url is not None and doc.pdf_url.endswith(".pdf?download=1")


def test_fetch_publication_falls_back_to_page_text_when_pdf_fails() -> None:
    url_map = {LANDING_URL: (200, LANDING_HTML.encode(), "text/html")}
    with _mock_client(_serve(url_map)) as client:  # PDF URLs 404
        doc = _fetch(LANDING_URL, client)
    assert doc.pdf_url is None
    assert doc.content and "insider threat mitigation programs" in doc.content


def test_fetch_publication_direct_pdf_url() -> None:
    url_map = {PDF_URL: (200, SAMPLE_PDF, "application/pdf")}
    with _mock_client(_serve(url_map)) as client:
        doc = _fetch(PDF_URL, client)
    assert doc.content == PDF_TEXT
    assert doc.pdf_url == PDF_URL
    assert doc.title == ""


def test_fetch_publication_respects_content_cap() -> None:
    url_map = {
        LANDING_URL: (200, LANDING_HTML.encode(), "text/html"),
        PDF_URL: (200, SAMPLE_PDF, "application/pdf"),
    }
    with _mock_client(_serve(url_map)) as client:
        doc = _fetch(LANDING_URL, client, content_max_chars=500)
    assert doc.content is not None and len(doc.content) <= 500


def test_fetch_publication_http_error_raises() -> None:
    with _mock_client(_serve({LANDING_URL: (403, b"", "text/html")})) as client:
        with pytest.raises(PublicationFetchError):
            _fetch(LANDING_URL, client)


def test_resolve_channel_publications() -> None:
    assert resolve_channel("pub-sei-common-sense-guide-7e") == "publications"
    assert resolve_channel("anything", "publications") == "publications"
    assert resolve_channel("social-reddit-foo") == "social"
    assert resolve_channel("reddit-tips") == "tips"


def test_get_publication_sources_filters_by_id() -> None:
    all_sources = get_publication_sources()
    assert all(s.id.startswith("pub-") for s in all_sources)
    one = get_publication_sources(["pub-sei-common-sense-guide-7e"])
    assert [s.id for s in one] == ["pub-sei-common-sense-guide-7e"]


def test_run_publications_ingestion_isolates_failures(tmp_path, monkeypatch) -> None:
    catalog = [
        PublicationSource(id="pub-good", name="Good Guide", url=LANDING_URL),
        PublicationSource(id="pub-bad", name="Blocked Guide", url="https://blocked.example/x"),
    ]
    monkeypatch.setattr(
        "apps.aggregator.publications_pipeline.get_publication_sources",
        lambda source_ids=None: catalog,
    )

    def fake_fetch(url, **kwargs):
        if url == LANDING_URL:
            url_map = {
                LANDING_URL: (200, LANDING_HTML.encode(), "text/html"),
                PDF_URL: (200, SAMPLE_PDF, "application/pdf"),
            }
            with _mock_client(_serve(url_map)) as client:
                return _fetch(LANDING_URL, client)
        raise PublicationFetchError(url, "HTTP 403")

    monkeypatch.setattr(
        "apps.aggregator.publications_pipeline.fetch_publication",
        fake_fetch,
    )

    store_path = tmp_path / "raw.jsonl"
    result = run_publications_ingestion(store_path=str(store_path))
    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.total_articles_saved == 1

    [article] = JsonlArticleStore(store_path).load_all()
    assert article.channel == "publications"
    assert article.source_id == "pub-good"
    assert article.content == PDF_TEXT

    # Re-run: unchanged doc dedupes to zero saves.
    rerun = run_publications_ingestion(store_path=str(store_path))
    assert rerun.total_articles_saved == 0


def test_ingest_publication_url_adhoc_source(tmp_path, monkeypatch) -> None:
    def fake_fetch(url, **kwargs):
        url_map = {
            LANDING_URL: (200, LANDING_HTML.encode(), "text/html"),
            PDF_URL: (200, SAMPLE_PDF, "application/pdf"),
        }
        with _mock_client(_serve(url_map)) as client:
            return _fetch(url, client)

    monkeypatch.setattr(
        "apps.aggregator.publications_pipeline.fetch_publication",
        fake_fetch,
    )
    store_path = tmp_path / "raw.jsonl"
    article = ingest_publication_url(LANDING_URL, store_path=str(store_path))
    assert article is not None
    assert article.channel == "publications"
    assert article.source_id == "pub-pubs-example-org"
    assert article.content == PDF_TEXT


def test_min_score_gate_exempts_publications(tmp_path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            RawArticle(
                title="Reference guide",
                link="https://pubs.example.org/guide",
                summary="A long reference document.",
                content="General guidance text with little keyword density.",
                source_id="pub-guide",
                source_name="Guide",
                channel="publications",
            ),
            RawArticle(
                title="Unrelated gardening news",
                link="https://news.example.org/gardening",
                summary="Tulip season starts early this year.",
                source_id="example-news",
                source_name="Example News",
            ),
        ]
    )
    result = run_processing(
        raw_path=raw_path,
        processed_path=processed_path,
        min_score=0.99,
    )
    assert result.articles_processed == 2
    from apps.aggregator.processed_storage import JsonlProcessedStore

    links = {a.link for a in JsonlProcessedStore(processed_path).load_all()}
    assert "https://pubs.example.org/guide" in links
    assert "https://news.example.org/gardening" not in links
