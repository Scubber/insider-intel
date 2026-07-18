"""Tests for the ingest summarizer (ai_summary + case_record + LLM ITM hits)."""

from __future__ import annotations

import json
from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
from shared.llm.base import CaseExtractionResult, ItmRef
from shared.schemas import CaseRecord, RawArticle


def _raw(**overrides: object) -> RawArticle:
    data = {
        "title": "Insider threat: data exfiltration via CVE-2024-11111",
        "link": "https://example.com/insider-alert",
        "summary": (
            "<p>Disgruntled employee used removable media after resignation. "
            "Domain badactor.io observed.</p>"
        ),
        "source_id": "example",
        "source_name": "Example",
    }
    data.update(overrides)
    return RawArticle.model_validate(data)


def _result(**overrides: object) -> CaseExtractionResult:
    data = {
        "ai_summary": "A departing engineer copied schematics to USB before resigning.",
        "is_insider_case": True,
        "actor_role": "departing engineer",
        "access_vector": "engineering file share",
        "methods": ["USB copy of design files"],
        "exfil_channels": ["USB drive"],
        "outcome": "charged under DTSA",
        "confidence": 0.9,
    }
    data.update(overrides)
    return CaseExtractionResult.model_validate(data)


class FakeSummarizer:
    model_name = "fake-model"

    def __init__(self, result: CaseExtractionResult | None = None) -> None:
        self.calls = 0
        self.result = result if result is not None else _result()

    def extract_case(self, *, title, source, text, itm_candidates):
        self.calls += 1
        self.last_candidates = itm_candidates
        return self.result


class ExplodingSummarizer(FakeSummarizer):
    def extract_case(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider down")


def _install(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        "shared.agents.summarize.get_summarizer_provider", lambda settings: provider
    )
    monkeypatch.setattr(
        "apps.aggregator.process_pipeline.get_summarizer_provider",
        lambda settings: provider,
    )


def test_provider_unset_is_a_noop() -> None:
    processed = process_article(_raw())
    assert processed.ai_summary is None
    assert processed.case_record is None
    assert all(h.source == "lexical" for h in processed.entities.itm_hits)


def test_non_qualifying_article_never_calls_provider(monkeypatch) -> None:
    fake = FakeSummarizer()
    _install(monkeypatch, fake)
    processed = process_article(
        _raw(
            title="Quarterly roadmap update",
            link="https://example.com/roadmap",
            summary="<p>The all-hands covered the roadmap and a new office.</p>",
        )
    )
    assert fake.calls == 0
    assert processed.case_record is None


def test_qualifying_article_gets_summary_record_and_llm_hits(monkeypatch) -> None:
    fake = FakeSummarizer(
        _result(
            itm_refs=[
                ItmRef(id="IF038", confidence=0.9, evidence="second job"),
                ItmRef(id="ZZ999", confidence=0.99),  # not in catalog → dropped
                ItmRef(id="AF001", confidence=0.2),  # below floor → dropped
            ]
        )
    )
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    assert "candidate" not in (fake.last_candidates or "").lower() or fake.last_candidates
    assert processed.ai_summary and "departing engineer" in processed.ai_summary
    record = processed.case_record
    assert record is not None and record.is_insider_case
    assert record.methods == ["USB copy of design files"]
    assert record.model == "fake-model"
    assert record.extracted_at is not None

    by_id = {h.id: h for h in processed.entities.itm_hits}
    assert "IF038" in by_id and by_id["IF038"].source == "llm"
    assert "ZZ999" not in by_id and "AF001" not in by_id
    # LLM hits join the filter/search signals and control resolution
    assert "IF038" in processed.entities.keywords_hit


def test_provider_failure_still_processes_article(monkeypatch) -> None:
    fake = ExplodingSummarizer()
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    assert processed.ai_summary is None
    assert processed.case_record is None
    assert processed.entities.itm_hits  # lexical pipeline unaffected


def test_carry_forward_never_rebills(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save([_raw()])

    fake = FakeSummarizer(_result(itm_refs=[ItmRef(id="IF038", confidence=0.9)]))
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1

    # Refresh the raw article → it is re-processed, but the paid-for fields
    # (summary, record, LLM ITM hit) must carry forward with zero new calls.
    updated = _raw(summary="<p>Employee also sabotaged backups before departure.</p>")
    assert store.refresh([updated]) == (0, 1)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 1
    row = rows[0]
    assert "sabotaged backups" in row.clean_text  # reprocess really happened
    assert row.ai_summary and row.case_record is not None
    assert any(h.id == "IF038" and h.source == "llm" for h in row.entities.itm_hits)


def test_backfill_converts_existing_corpus_bounded_by_cap(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    links = [f"https://example.com/case-{n}" for n in range(3)]
    JsonlArticleStore(raw_path).save([_raw(link=link) for link in links])

    # First run without any provider: rows exist, no records (pre-feature corpus).
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert all(r.case_record is None for r in JsonlProcessedStore(processed_path).load_all())

    # Provider appears with a 2-call budget: backfill sweeps newest-first.
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "2")
    fake = FakeSummarizer()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 2
    rows = JsonlProcessedStore(processed_path).load_all()
    assert sum(1 for r in rows if r.case_record is not None) == 2

    # Next run finishes the remainder without re-billing the converted rows.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 3
    rows = JsonlProcessedStore(processed_path).load_all()
    assert all(r.case_record is not None for r in rows)


def test_case_record_sanitization_clamps() -> None:
    record = CaseRecord(
        actor_role="x" * 500,
        methods=[f"method {i}\x00\x01" for i in range(20)],
        motive_signals=["dup", "DUP", "  dup  "],
    )
    clean = record.sanitized()
    assert len(clean.actor_role) == 200
    assert len(clean.methods) == 8
    assert all("\x00" not in m for m in clean.methods)
    assert clean.motive_signals == ["dup"]


def test_pre_feature_jsonl_row_still_loads(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    row = json.loads(processed_path.read_text().splitlines()[0])
    row.pop("case_record", None)
    for hit in row.get("entities", {}).get("itm_hits", []):
        hit.pop("source", None)
    processed_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 1
    assert rows[0].case_record is None
    assert all(h.source == "lexical" for h in rows[0].entities.itm_hits)


def test_search_hit_carries_summary_and_record() -> None:
    from apps.search.index import ArticleSearchIndex

    processed = process_article(_raw())
    enriched = processed.model_copy(
        update={
            "ai_summary": "Analyst summary.",
            "case_record": CaseRecord(is_insider_case=True, methods=["USB copy"]),
        }
    )
    hit = ArticleSearchIndex._to_hit(enriched, 1.0)
    assert hit.ai_summary == "Analyst summary."
    assert hit.case_record is not None and hit.case_record.methods == ["USB copy"]


def test_filings_get_the_bigger_prompt_budget(monkeypatch) -> None:
    received: dict[str, int] = {}

    class CapProbe(FakeSummarizer):
        def extract_case(self, *, title, source, text, itm_candidates):
            received[source] = len(text)
            return super().extract_case(
                title=title, source=source, text=text, itm_candidates=itm_candidates
            )

    fake = CapProbe()
    _install(monkeypatch, fake)
    body = "The defendant copied trade secret files to a personal drive. " * 700

    process_article(
        _raw(
            title="United States v. Example insider threat",
            link="https://www.courtlistener.com/docket/9/us-v-example/",
            summary="Court: SDNY",
            content=f"CourtListener query: q\n{body}",
            source_id="courtlistener-recap",
        )
    )
    process_article(_raw(content=body, link="https://example.com/news-cap"))

    assert received["courtlistener-recap"] > 6000  # filings budget (24k default)
    assert received["example"] <= 6000  # news budget unchanged
