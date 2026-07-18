"""Tests for the two-stage deep extraction pipeline (stubbed LLM throughout)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import deep_extract, service, ttp_extract
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


@pytest.fixture(autouse=True)
def _fresh_cache():
    deep_extract.clear_stage1_cache()
    yield
    deep_extract.clear_stage1_cache()


def _cfg(**overrides) -> Settings:
    return Settings(CORS_ORIGINS="http://127.0.0.1:5500", **overrides)


def _article(title: str, link: str, *, content: str = "", channel: str = "news"):
    return process_article(
        RawArticle(
            title=title,
            link=link,
            summary="Disgruntled employee used removable media for data exfiltration.",
            content=content,
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="courtlistener-recap" if channel == "filings" else "example",
            source_name="Example",
            channel=channel,
        )
    )


def _index_for(tmp_path, articles):
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(articles)
    return service.get_index(path, reload=True)


def _stage1_reply(link: str) -> dict:
    return {
        "actor_profile": "departing engineer with repo access",
        "timeline": ["2024-01: began syncing files", "2024-02: resigned"],
        "methods": [
            {
                "action": "synced 9,700 design files to a personal Dropbox",
                "tools": ["Dropbox"],
                "quantity": "9,700 files",
                "observables": [
                    {
                        "description": "Bulk uploads to dropbox.com",
                        "artifact": "proxy/egress logs",
                        "channel": "cloud",
                    },
                    {
                        "description": "Personal-account browser sessions on the corp laptop",
                        "artifact": "EDR browser telemetry",
                        "channel": "endpoint",
                    },
                ],
            }
        ],
        "detection": "forensic review of the returned laptop",
        "outcome": "civil suit",
        "candidate_technique_ids": ["IF002"],
        "hunt_terms": ["dropbox.com/home", "design_files.zip"],
    }


def _synthesis_reply(link: str) -> dict:
    return {
        "summary": "Departing employees exfiltrated data via personal cloud storage.",
        "techniques": [
            {
                "id": "IF002",
                "tradecraft_summary": "Insiders bulk-sync corporate files to personal cloud "
                "accounts shortly before resignation.",
                "cases": [
                    {
                        "link": link,
                        "tradecraft": "Synced 9,700 files to Dropbox before resigning.",
                        "bullets": ["9,700 design files to personal Dropbox"],
                    }
                ],
                "observables": [
                    {
                        "description": "Bulk uploads to consumer cloud domains",
                        "artifact": "proxy/egress logs",
                        "channel": "cloud",
                    }
                ],
                "hunt_queries": [
                    {
                        "stack": "Splunk/SIEM",
                        "logic": "index=proxy dest_domain IN (dropbox.com) bytes_out>100MB",
                        "rationale": "catches the bulk sync pattern from the cases",
                    }
                ],
            }
        ],
    }


def test_two_stage_report_attaches_catalog_controls(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/case-a")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            return _stage1_reply(article.link)
        return _synthesis_reply(article.link)

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)

    assert report.mode == "llm"
    assert report.report_version == 2
    section = next(s for s in report.techniques if s.id == "IF002")
    assert section.tradecraft_summary
    assert section.cases[0].tradecraft
    assert section.observables[0].channel == "cloud"
    # DT*/PV* are attached from the catalog in code, never from the LLM.
    from shared.itm.index import load_itm_index

    tech = next(t for t in load_itm_index().techniques if t.id == "IF002")
    assert [c.id for c in section.detection.detections] == sorted({r.id for r in tech.detections})
    assert [c.id for c in section.detection.preventions] == sorted({r.id for r in tech.preventions})
    assert section.detection.hunt_queries[0].logic.startswith("index=proxy")
    assert section.theme == tech.theme
    # Legacy fields derive from the new structure (cloud → network bucket).
    assert any("Bulk uploads" in cue for cue in report.network)
    assert "dropbox.com/home" in report.seeds


def test_synthesis_drops_fake_ids_and_keeps_floor_sections(tmp_path, monkeypatch) -> None:
    article = _article(
        "Insider threat: engineer exfiltrated trade secrets", "https://example.com/case-b"
    )
    assert article.entities.itm_hits  # lexical floor has something to keep
    floor_id = article.entities.itm_hits[0].id.upper()
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            return _stage1_reply(article.link)
        return {
            "summary": "s",
            "techniques": [{"id": "ZZ999", "cases": [{"link": article.link, "bullets": ["fake"]}]}],
        }

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    ids = {s.id for s in report.techniques}
    assert "ZZ999" not in ids
    assert floor_id in ids  # lexically-hit technique survives the LLM dropping it


def test_deep_cap_limits_stage1_calls_and_floor_fills_rest(tmp_path, monkeypatch) -> None:
    articles = [
        _article(f"Case {n}", f"https://example.com/cap-{n}", content="body " * (n + 1) * 100)
        for n in range(5)
    ]
    index = _index_for(tmp_path, articles)
    cfg = _cfg(ANTHROPIC_API_KEY="a", EXTRACT_DEEP_MAX_ARTICLES=2)

    stage1_calls: list[str] = []

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            stage1_calls.append(user)
            return _stage1_reply("")
        return _synthesis_reply(articles[0].link)

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [a.link for a in articles], settings=cfg)
    assert len(stage1_calls) == 2  # cap enforced
    assert "2 deep / 3 floor" in report.detail


def test_deep_max_zero_is_synthesis_only(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/case-zero")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a", EXTRACT_DEEP_MAX_ARTICLES=0)

    systems: list[str] = []

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        systems.append(system)
        return _synthesis_reply(article.link)

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    assert systems == [deep_extract.STAGE2_SYSTEM_PROMPT]  # exactly one call
    assert report.mode == "llm"
    assert "0 deep / 1 floor" in report.detail


def test_stage1_failure_still_yields_llm_report(tmp_path, monkeypatch) -> None:
    articles = [
        _article("Case ok", "https://example.com/ok", content="x" * 5000),
        _article("Case bad", "https://example.com/bad", content="y" * 4000),
    ]
    index = _index_for(tmp_path, [articles[0], articles[1]])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            if "example.com/bad" in user:
                raise RuntimeError("boom")
            return _stage1_reply("")
        return _synthesis_reply(articles[0].link)

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [a.link for a in articles], settings=cfg)
    assert report.mode == "llm"
    assert "1 deep / 1 floor" in report.detail
    assert "1 deep extraction(s) failed" in report.detail


def test_stage2_failure_falls_back_to_mechanical_sections(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/mech")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            return _stage1_reply(article.link)
        raise RuntimeError("synthesis provider down")

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    assert report.mode == "llm"
    assert "Synthesis failed" in report.detail
    section = next(s for s in report.techniques if s.id == "IF002")
    # Mechanical assembly: bullets are the extracted method actions verbatim.
    assert section.cases[0].bullets == ["synced 9,700 design files to a personal Dropbox"]
    assert not section.tradecraft_summary
    assert section.detection.detections  # controls still attach post-hoc
    assert section.observables


def test_total_llm_failure_returns_floor(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/floor-only")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        raise RuntimeError("everything down")

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    assert report.mode == "seeds"
    assert report.report_version == 1
    assert "LLM failed" in report.detail


def test_malformed_stage_json_never_500s(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/malformed")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        # Wrong types everywhere — coercion must drop fields, not raise.
        return {
            "summary": 42,
            "methods": [{"action": 1}, "not-a-dict", {"action": "ok", "observables": "nope"}],
            "techniques": [{"id": 3}, {"id": "IF002", "cases": "nope"}],
            "candidate_technique_ids": "IF002",
            "hunt_terms": [None, "ok-term"],
        }

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    assert report.mode in ("llm", "seeds")  # degraded, but a response


def test_stage1_cache_hits_on_same_processed_at(tmp_path, monkeypatch) -> None:
    article = _article("US v. Example", "https://example.com/cached")
    index = _index_for(tmp_path, [article])
    cfg = _cfg(ANTHROPIC_API_KEY="a")

    stage1_calls = 0

    def fake_call(*, provider, model, system, user, cfg, max_tokens=2000):
        nonlocal stage1_calls
        if system is deep_extract.STAGE1_SYSTEM_PROMPT:
            stage1_calls += 1
            return _stage1_reply(article.link)
        return _synthesis_reply(article.link)

    monkeypatch.setattr(ttp_extract, "_call_extract_llm", fake_call)
    monkeypatch.setattr(ttp_extract, "enrich_courtlistener_snippet", lambda *a, **k: "")
    ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    ttp_extract.extract_ttps_for_links(index, [article.link], settings=cfg)
    assert stage1_calls == 1  # second report reused the cached extraction

    # A reprocessed article (new processed_at) misses cleanly.
    reprocessed = article.model_copy(update={"processed_at": datetime.now(tz=UTC)})
    assert deep_extract.cache_get(reprocessed) is None


def test_forensics_from_floor_reshapes_case_record() -> None:
    from shared.schemas import CaseRecord

    article = _article("US v. Floor", "https://example.com/from-floor").model_copy(
        update={
            "case_record": CaseRecord(
                is_insider_case=True,
                actor_role="contractor sysadmin",
                access_vector="privileged VPN access",
                methods=["dumped the customer database"],
                exfil_channels=["personal Gmail"],
                detection_trigger="DLP alert",
                outcome="indicted",
            )
        }
    )
    record = deep_extract.forensics_from_floor(article)
    assert record.extraction_status == "floor"
    assert "contractor sysadmin" in record.actor_profile
    actions = [m.action for m in record.methods]
    assert "dumped the customer database" in actions
    assert any("personal Gmail" in a for a in actions)
    assert record.detection == "DLP alert"
    assert record.candidate_technique_ids == [h.id.upper() for h in article.entities.itm_hits or []]


def test_chat_completion_backcompat_without_max_tokens(monkeypatch) -> None:
    from shared.llm import openai_provider

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setattr(openai_provider.httpx, "post", fake_post)
    out = openai_provider._chat_completion(
        base_url="http://x/v1", model="m", api_key=None, timeout=5, system="s", user="u"
    )
    assert out == "{}"
    assert "max_tokens" not in captured  # classifier/summarizer callers unchanged

    openai_provider._chat_completion(
        base_url="http://x/v1",
        model="m",
        api_key=None,
        timeout=5,
        system="s",
        user="u",
        max_tokens=1234,
    )
    assert captured["max_tokens"] == 1234
