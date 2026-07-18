"""Tests for POST /extract/ttps seed-floor extraction."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


def test_extract_ttps_seed_floor_without_xai(tmp_path, monkeypatch) -> None:
    article = process_article(
        RawArticle(
            title="Employee moonlighting and undisclosed concurrent employment dispute",
            link="https://example.com/moonlighting-case",
            summary="Outside employment policy and dual employment allegations.",
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([article])

    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        CORS_ORIGINS="http://127.0.0.1:5500",
        XAI_API_KEY=None,
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    service.get_index(path, reload=True)

    client = TestClient(app)
    res = client.post(
        "/extract/ttps",
        json={"links": ["https://example.com/moonlighting-case"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "seeds"
    assert body["article_count"] == 1
    assert body["titles"] == ["Employee moonlighting and undisclosed concurrent employment dispute"]
    assert any(b["id"] == "TTP-OE-01" for b in body["behaviors"])
    assert body["email"]
    assert body["human"]
    assert body["matched_if038"] is True


def _processed(title: str, link: str, summary: str):
    return process_article(
        RawArticle(
            title=title,
            link=link,
            summary=summary,
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )


def test_seed_floor_without_if038_reports_actual_techniques() -> None:
    from apps.search.ttp_extract import seed_floor_report

    article = _processed(
        "Insider threat: engineer exfiltrated trade secrets",
        "https://example.com/exfil-case",
        "Disgruntled employee used removable media for data exfiltration.",
    )
    assert article.entities.itm_hits
    assert not any(h.id.upper() == "IF038" for h in article.entities.itm_hits)

    report = seed_floor_report([article])
    assert report.matched_if038 is False
    ids = {b.id for b in report.behaviors}
    # No overemployment placebo pack — behaviors come from the article's hits.
    assert not any(i.startswith("TTP-OE") for i in ids)
    assert {h.id for h in article.entities.itm_hits} & ids


def test_seed_floor_case_record_feeds_behaviors_and_seeds() -> None:
    from apps.search.ttp_extract import _article_text_pack, seed_floor_report
    from shared.schemas import CaseRecord

    article = _processed(
        "Insider threat: engineer exfiltrated trade secrets",
        "https://example.com/exfil-case-2",
        "Disgruntled employee used removable media for data exfiltration.",
    ).model_copy(
        update={
            "case_record": CaseRecord(
                is_insider_case=True,
                actor_role="departing engineer",
                methods=["rclone sync to personal cloud"],
                exfil_channels=["personal Gmail"],
                detection_trigger="DLP alert on outbound mail",
            )
        }
    )
    report = seed_floor_report([article])
    assert any(b.id.startswith("CASE-") for b in report.behaviors)
    assert "rclone sync to personal cloud" in report.seeds
    assert "personal Gmailil" not in report.seeds  # sanity: exact strings only
    assert "personal Gmail" in report.network
    assert "DLP alert on outbound mail" in report.human

    pack = _article_text_pack(article)
    assert "Case record:" in pack
    assert "- actor_role: departing engineer" in pack
    assert pack.index("Case record:") < pack.index("Text:")


def test_seed_floor_empty_evidence_labels_generic_fallback() -> None:
    from apps.search.ttp_extract import seed_floor_report

    article = _processed(
        "Quarterly roadmap update",
        "https://example.com/roadmap",
        "The all-hands covered the roadmap and a new office.",
    )
    assert not article.entities.itm_hits
    report = seed_floor_report([article])
    assert any(b.id == "TTP-OE-01" for b in report.behaviors)  # never-empty floor
    assert report.matched_if038 is False
    assert "no matched evidence" in report.detail


def test_seed_floor_builds_technique_sections_with_case_bullets() -> None:
    from apps.search.ttp_extract import seed_floor_report
    from shared.schemas import CaseRecord

    article = _processed(
        "DictateMD, Inc. v. Ahmadi",
        "https://example.com/dictatemd",
        "Departing employee accused of trade secret theft and data exfiltration.",
    ).model_copy(
        update={
            "case_record": CaseRecord(
                is_insider_case=True,
                methods=["downloaded customer database before resignation"],
                exfil_channels=["personal Dropbox"],
                detection_trigger="forensic review of the laptop",
            )
        }
    )
    assert article.entities.itm_hits
    report = seed_floor_report([article])

    assert report.techniques
    section = report.techniques[0]
    assert section.id == article.entities.itm_hits[0].id.upper()
    assert section.description
    assert section.cases and section.cases[0].title == "DictateMD, Inc. v. Ahmadi"
    bullets = section.cases[0].bullets
    assert "downloaded customer database before resignation" in bullets
    assert any(b.startswith("Exfil channel:") for b in bullets)
    assert any(b.startswith("Detected via:") for b in bullets)
    assert report.summary and "ITM technique" in report.summary
    # Honest labeling — no "seed pack / no XAI_API_KEY" wording for evidence.
    assert "Evidence pack" in report.detail
    assert "XAI_API_KEY" not in report.detail


def test_extract_ttps_endpoint_reports_llm_off(tmp_path, monkeypatch) -> None:
    article = process_article(
        RawArticle(
            title="Insider threat: engineer exfiltrated trade secrets",
            link="https://example.com/exfil-endpoint",
            summary="Disgruntled employee used removable media for data exfiltration.",
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([article])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        CORS_ORIGINS="http://127.0.0.1:5500",
        XAI_API_KEY=None,
        ANTHROPIC_API_KEY=None,
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    service.get_index(path, reload=True)

    client = TestClient(app)
    res = client.post("/extract/ttps", json={"links": [article.link]})
    assert res.status_code == 200
    body = res.json()
    assert body["techniques"]
    assert "LLM off" in body["detail"]


def test_resolve_extract_providers_auto_and_explicit() -> None:
    from apps.search.ttp_extract import resolve_extract_providers

    base = dict(CORS_ORIGINS="http://127.0.0.1:5500")
    assert resolve_extract_providers(Settings(**base)) == []
    assert resolve_extract_providers(Settings(**base, XAI_API_KEY="x"))[0][0] == "xai"
    assert resolve_extract_providers(Settings(**base, ANTHROPIC_API_KEY="a"))[0][0] == "anthropic"
    # auto lists every configured key in preference order.
    many = Settings(
        **base, XAI_API_KEY="x", ANTHROPIC_API_KEY="a", GEMINI_API_KEY="g", OPENAI_API_KEY="o"
    )
    assert [p for p, _ in resolve_extract_providers(many)] == [
        "xai",
        "anthropic",
        "gemini",
        "openai",
    ]
    forced = Settings(
        **base, XAI_API_KEY="x", ANTHROPIC_API_KEY="a", EXTRACT_LLM_PROVIDER="anthropic"
    )
    assert [p for p, _ in resolve_extract_providers(forced)] == ["anthropic"]
    off = Settings(**base, XAI_API_KEY="x", EXTRACT_LLM_PROVIDER="none")
    assert resolve_extract_providers(off) == []
    # A bare OPENAI_API_KEY retargets the openai provider to real OpenAI.
    openai_only = Settings(**base, OPENAI_API_KEY="o")
    assert resolve_extract_providers(openai_only) == [("openai", "gpt-4o-mini")]
    gemini_only = Settings(**base, GEMINI_API_KEY="g")
    assert resolve_extract_providers(gemini_only) == [("gemini", "gemini-2.5-flash")]


def test_extract_falls_through_failing_providers(tmp_path, monkeypatch) -> None:
    from apps.search import ttp_extract

    article = process_article(
        RawArticle(
            title="Insider threat: engineer exfiltrated trade secrets",
            link="https://example.com/fallback-case",
            summary="Disgruntled employee used removable media for data exfiltration.",
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([article])
    index = service.get_index(path, reload=True)

    settings = Settings(
        CORS_ORIGINS="http://127.0.0.1:5500",
        ANTHROPIC_API_KEY="broke-account",  # pragma: allowlist secret
        GEMINI_API_KEY="works",  # pragma: allowlist secret
    )

    calls: list[str] = []

    def fake_call(*, provider, model, system, user, cfg):
        calls.append(provider)
        if provider == "anthropic":
            raise RuntimeError("credit balance is too low")
        return {"summary": "Gemini wrote this.", "behaviors": [], "techniques": []}

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=settings)
    assert calls == ["anthropic", "gemini"]
    assert report.mode == "llm"
    assert report.summary == "Gemini wrote this."
    assert "gemini" in report.detail


def test_resolve_openai_and_gemini_compat_defaults() -> None:
    from shared.llm import resolve_gemini_compat, resolve_openai_compat

    base = dict(CORS_ORIGINS="http://127.0.0.1:5500")
    # Bare OPENAI_API_KEY swaps the local-Ollama defaults for real OpenAI.
    url, model, key = resolve_openai_compat(Settings(**base, OPENAI_API_KEY="sk-x"))
    assert url == "https://api.openai.com/v1"
    assert model == "gpt-4o-mini"
    assert key == "sk-x"
    # Explicit OPENAI_COMPAT_* values always win.
    url2, model2, key2 = resolve_openai_compat(
        Settings(
            **base,
            OPENAI_API_KEY="sk-x",  # pragma: allowlist secret
            OPENAI_COMPAT_BASE_URL="http://myhost:8080/v1",
            OPENAI_COMPAT_MODEL="mistral",
        )
    )
    assert url2 == "http://myhost:8080/v1"
    assert model2 == "mistral"
    # No key at all keeps the local defaults (Ollama).
    url3, _, key3 = resolve_openai_compat(Settings(**base))
    assert url3 == "http://localhost:11434/v1"
    assert key3 is None
    gurl, gmodel, gkey = resolve_gemini_compat(Settings(**base, GEMINI_API_KEY="g"))
    assert gurl.startswith("https://generativelanguage.googleapis.com")
    assert gmodel == "gemini-2.5-flash"
    assert gkey == "g"


def test_merge_llm_sections_validates_ids_and_resolves_links() -> None:
    from apps.search.ttp_extract import _merge_llm_into_floor, seed_floor_report

    article = _processed(
        "DictateMD, Inc. v. Ahmadi",
        "https://example.com/dictatemd-merge",
        "Departing employee accused of trade secret theft and data exfiltration.",
    )
    floor = seed_floor_report([article])
    real_id = floor.techniques[0].id
    llm = {
        "summary": "One departing employee stole trade secrets.",
        "techniques": [
            {
                "id": real_id,
                "cases": [
                    {
                        "title": "DictateMD v. Ahmadi",
                        "bullets": ["Synced the customer list to a personal drive"],
                    }
                ],
            },
            {"id": "ZZ999", "cases": [{"title": "Fake", "bullets": ["invented"]}]},
        ],
    }
    merged = _merge_llm_into_floor(
        floor, llm, links_by_title={article.title: article.link}, provider="anthropic"
    )
    assert merged.mode == "llm"
    assert merged.summary == "One departing employee stole trade secrets."
    ids = {s.id for s in merged.techniques}
    assert real_id in ids and "ZZ999" not in ids
    enriched = next(s for s in merged.techniques if s.id == real_id)
    # Loose title match resolves back to the board link + canonical title.
    assert enriched.cases[0].link == article.link
    assert enriched.cases[0].title == article.title
    assert enriched.cases[0].bullets == ["Synced the customer list to a personal drive"]
    assert "anthropic" in merged.detail


def test_articles_text_endpoint_returns_filing_body(tmp_path, monkeypatch) -> None:
    from apps.aggregator.storage import JsonlArticleStore

    body = "The defendant exfiltrated schematics to a personal drive."
    raw = RawArticle(
        title="United States v. Example",
        link="https://www.courtlistener.com/docket/9/us-v-example/",
        summary="Court: SDNY",
        content=f"CourtListener query: insider\n--- Document 1: Complaint ---\n{body}",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )
    filing = process_article(raw)
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([filing])
    raw_path = tmp_path / "raw.jsonl"
    JsonlArticleStore(raw_path).save([raw])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(raw_path),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    service.get_index(path, reload=True)

    client = TestClient(app)
    res = client.get("/articles/text", params={"link": filing.link})
    assert res.status_code == 200
    data = res.json()
    assert data["channel"] == "filings"
    assert body in data["text"]
    assert "CourtListener query:" not in data["text"]
    assert "--- Document 1: Complaint ---" in data["text"]  # raw line breaks kept

    missing = client.get("/articles/text", params={"link": "https://example.com/nope"})
    assert missing.status_code == 404


def test_filings_pack_carries_full_document_text() -> None:
    from apps.search.ttp_extract import FILINGS_TEXT_MAX_CHARS, MAX_TEXT_CHARS, _article_text_pack

    body = "The defendant exfiltrated schematics. " * 500  # ~19k chars
    filing = process_article(
        RawArticle(
            title="United States v. Example",
            link="https://www.courtlistener.com/docket/9/us-v-example/",
            summary="Court: SDNY\nDocket: 1:24-cr-00001",
            content=f"CourtListener query: q\n{body}",
            source_id="courtlistener-recap",
            source_name="CourtListener RECAP",
            channel="filings",
        )
    )
    pack = _article_text_pack(filing)
    assert len(pack) > MAX_TEXT_CHARS  # filings are not clipped to the news cap
    assert len(pack) <= FILINGS_TEXT_MAX_CHARS

    news = process_article(
        RawArticle(
            title="Insider threat news",
            link="https://example.com/news",
            summary="Data exfiltration by an employee.",
            content=body,
            source_id="example",
            source_name="Example",
        )
    )
    assert len(_article_text_pack(news)) <= MAX_TEXT_CHARS
