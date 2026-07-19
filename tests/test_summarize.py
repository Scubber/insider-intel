"""Tests for the unified ingest enricher (ai_summary + forensics + ITM hits)."""

from __future__ import annotations

import json
from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
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


def _reply(**overrides: object) -> dict:
    """The unified enricher's raw JSON reply (analyst note + forensic record)."""
    data: dict = {
        "ai_summary": "A departing engineer copied schematics to USB before resigning.",
        "is_insider_case": True,
        "confidence": 0.9,
        "source_type": "court_filing",
        "legal_posture": "indictment",
        "actor_profile": "departing engineer — engineering file share",
        "actor_role": "departing engineer",
        "access_vector": "engineering file share",
        "methods": [
            {
                "action": "USB copy of design files",
                "tools": ["USB drive"],
                "claim_status": "alleged",
                "evidence_quote": "copied the design files to a personal USB drive",
                "observables": [
                    {
                        "description": "mass file copy to removable media",
                        "artifact": "EDR removable-media events",
                        "channel": "endpoint",
                        "basis": "mechanically_implied",
                    }
                ],
            }
        ],
        "exfil_channels": ["USB drive"],
        "outcome": "charged under DTSA",
        "hunt_terms": ["design_files.zip"],
        "hunt_queries": [
            {"stack": "EDR", "logic": "device_type=USB action=file_write", "rationale": "USB copy"}
        ],
        "itm_refs": [],
    }
    data.update(overrides)
    return data


class FakeEnricher:
    model_name = "fake-model"

    def __init__(self, reply: dict | None = None) -> None:
        self.calls = 0
        self.reply = reply if reply is not None else _reply()

    def extract_case(self, *, title, source, text, itm_candidates):
        self.calls += 1
        self.last_candidates = itm_candidates
        return self.reply


class ExplodingEnricher(FakeEnricher):
    def extract_case(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider down")


def _install(monkeypatch, provider) -> None:
    # enrich_fields iterates the provider chain; the backfill gate still checks
    # get_summarizer_provider, so patch both to the fake.
    monkeypatch.setattr(
        "shared.agents.summarize.get_summarizer_chain", lambda settings: [provider]
    )
    monkeypatch.setattr(
        "apps.aggregator.process_pipeline.get_summarizer_provider",
        lambda settings: provider,
    )


def test_filing_with_full_text_qualifies_without_lexical_hit() -> None:
    """Full-text court filings qualify even with no ITM/use-case signal."""
    from shared.agents.summarize import qualifies

    body = "x" * 1_500
    # A real document body → qualifies on the filings branch.
    assert qualifies(itm_hits=[], use_cases=[], channel="filings", text=body)
    # A docket-entry stub → below the threshold → does not qualify.
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text="INDICTMENT")
    # News with the same empty signal never rides the filings branch.
    assert not qualifies(itm_hits=[], use_cases=[], channel="news", text=body)
    # A lexical hit still qualifies regardless of channel/text.
    assert qualifies(itm_hits=["IF002"], use_cases=[], channel="news", text="")


def test_article_qualifies_reads_channel_and_text() -> None:
    """The backfill-path wrapper resolves channel + clean_text from the row."""
    from types import SimpleNamespace

    from shared.agents.summarize import article_qualifies

    entities = SimpleNamespace(itm_hits=[])
    full = SimpleNamespace(
        source_id="courtlistener-recap", clean_text="y" * 2_000, use_cases=[], entities=entities
    )
    stub = SimpleNamespace(
        source_id="courtlistener-recap", clean_text="COMPLAINT", use_cases=[], entities=entities
    )
    assert article_qualifies(full)
    assert not article_qualifies(stub)
    # Threshold is tunable; 0 enriches every filing.
    assert article_qualifies(stub, filing_min_chars=0)


def test_provider_unset_is_a_noop() -> None:
    processed = process_article(_raw())
    assert processed.ai_summary is None
    assert processed.case_record is None
    assert processed.forensics is None
    assert all(h.source == "lexical" for h in processed.entities.itm_hits)


def test_non_qualifying_article_never_calls_provider(monkeypatch) -> None:
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    processed = process_article(
        _raw(
            title="Quarterly roadmap update",
            link="https://example.com/roadmap",
            summary="<p>The all-hands covered the roadmap and a new office.</p>",
        )
    )
    assert fake.calls == 0
    assert processed.forensics is None


def test_qualifying_article_gets_note_forensics_and_llm_hits(monkeypatch) -> None:
    fake = FakeEnricher(
        _reply(
            itm_refs=[
                {"id": "IF038", "confidence": 0.9, "evidence": "second job"},
                {"id": "ZZ999", "confidence": 0.99},  # not in catalog → dropped
                {"id": "AF001", "confidence": 0.2},  # below floor → dropped
            ]
        )
    )
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    assert processed.ai_summary and "departing engineer" in processed.ai_summary

    forensics = processed.forensics
    assert forensics is not None and forensics.is_insider_case
    assert forensics.extraction_status == "llm"
    assert forensics.methods and forensics.methods[0].action == "USB copy of design files"
    assert forensics.hunt_queries and forensics.hunt_queries[0].stack == "EDR"
    assert forensics.link == processed.link and forensics.model == "fake-model"
    # candidate_technique_ids are stamped from the final merged ITM hits.
    assert "IF038" in forensics.candidate_technique_ids

    # Legacy CaseRecord is derived from the forensic record for UI back-compat.
    record = processed.case_record
    assert record is not None and record.is_insider_case
    assert record.methods == ["USB copy of design files"]
    assert record.model == "fake-model" and record.extracted_at is not None

    by_id = {h.id: h for h in processed.entities.itm_hits}
    assert "IF038" in by_id and by_id["IF038"].source == "llm"
    assert "ZZ999" not in by_id and "AF001" not in by_id
    assert "IF038" in processed.entities.keywords_hit


def test_evidence_rigor_fields_persist(monkeypatch) -> None:
    """claim_status / evidence_quote / observable basis / posture round-trip."""
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    forensics = process_article(_raw()).forensics
    assert forensics is not None
    assert forensics.source_type == "court_filing"
    assert forensics.legal_posture == "indictment"
    method = forensics.methods[0]
    assert method.claim_status == "alleged"
    assert "USB" in method.evidence_quote
    assert method.observables[0].basis == "mechanically_implied"


def test_evidence_rigor_bad_values_fall_back_to_safe_defaults(monkeypatch) -> None:
    """Unknown enums degrade to the weaker/unknown default, never raise."""
    fake = FakeEnricher(
        _reply(
            source_type="tabloid",  # not in the allowed set
            legal_posture="vibes",  # not in the allowed set
            methods=[
                {
                    "action": "USB copy of design files",
                    "claim_status": "definitely",  # invalid enum
                    "observables": [
                        {"description": "file copy", "basis": "hunch"}  # invalid enum
                    ],
                }
            ],
        )
    )
    _install(monkeypatch, fake)
    forensics = process_article(_raw()).forensics
    assert forensics is not None
    assert forensics.source_type == "unknown"
    assert forensics.legal_posture == "unknown"
    method = forensics.methods[0]
    assert method.claim_status == "unclear"
    assert method.evidence_quote == ""
    assert method.observables[0].basis == "analyst_inference"


def test_provider_failure_still_processes_article(monkeypatch) -> None:
    fake = ExplodingEnricher()
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    assert processed.ai_summary is None
    assert processed.case_record is None
    assert processed.forensics is None
    assert processed.entities.itm_hits  # lexical pipeline unaffected


def test_carry_forward_never_rebills(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save([_raw()])

    fake = FakeEnricher(_reply(itm_refs=[{"id": "IF038", "confidence": 0.9}]))
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1

    # Refresh the raw article → it is re-processed, but the paid-for fields
    # (note, forensics, LLM ITM hit) must carry forward with zero new calls.
    updated = _raw(summary="<p>Employee also sabotaged backups before departure.</p>")
    assert store.refresh([updated]) == (0, 1)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 1
    row = rows[0]
    assert "sabotaged backups" in row.clean_text  # reprocess really happened
    assert row.ai_summary and row.forensics is not None and row.case_record is not None
    assert any(h.id == "IF038" and h.source == "llm" for h in row.entities.itm_hits)


def test_backfill_converts_existing_corpus_bounded_by_cap(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    links = [f"https://example.com/case-{n}" for n in range(3)]
    JsonlArticleStore(raw_path).save([_raw(link=link) for link in links])

    # First run without any provider: rows exist, no records (pre-feature corpus).
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert all(r.forensics is None for r in JsonlProcessedStore(processed_path).load_all())

    # Provider appears with a 2-call budget: backfill sweeps newest-first.
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "2")
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 2
    rows = JsonlProcessedStore(processed_path).load_all()
    assert sum(1 for r in rows if r.forensics is not None) == 2

    # Next run finishes the remainder without re-billing the converted rows.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 3
    rows = JsonlProcessedStore(processed_path).load_all()
    assert all(r.forensics is not None for r in rows)


def test_backfill_upgrades_legacy_rows_when_enabled(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    # Simulate a legacy row: case_record present, forensics absent (old summarizer).
    store = JsonlProcessedStore(processed_path)
    row = store.load_all()[0]
    legacy = row.model_copy(
        update={"case_record": CaseRecord(is_insider_case=True, methods=["old method"])}
    )
    store.upsert([legacy])
    assert store.load_all()[0].forensics is None

    fake = FakeEnricher()
    _install(monkeypatch, fake)

    # Upgrade OFF → legacy row is left untouched (never re-billed).
    monkeypatch.setenv("SUMMARIZER_UPGRADE_LEGACY", "0")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 0
    assert store.load_all()[0].forensics is None

    # Upgrade ON → the legacy row is re-billed once to add the forensic record.
    monkeypatch.setenv("SUMMARIZER_UPGRADE_LEGACY", "1")
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1
    upgraded = store.load_all()[0]
    assert upgraded.forensics is not None and upgraded.forensics.extraction_status == "llm"


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
    row.pop("forensics", None)
    for hit in row.get("entities", {}).get("itm_hits", []):
        hit.pop("source", None)
    processed_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 1
    assert rows[0].case_record is None and rows[0].forensics is None
    assert all(h.source == "lexical" for h in rows[0].entities.itm_hits)


def test_search_hit_carries_summary_record_and_forensics() -> None:
    from apps.search.index import ArticleSearchIndex
    from shared.schemas.forensics import PerCaseForensics

    processed = process_article(_raw())
    enriched = processed.model_copy(
        update={
            "ai_summary": "Analyst summary.",
            "case_record": CaseRecord(is_insider_case=True, methods=["USB copy"]),
            "forensics": PerCaseForensics(link=processed.link, title=processed.title),
        }
    )
    hit = ArticleSearchIndex._to_hit(enriched, 1.0)
    assert hit.ai_summary == "Analyst summary."
    assert hit.case_record is not None and hit.case_record.methods == ["USB copy"]
    assert hit.forensics is not None and hit.forensics.link == processed.link


def test_filings_get_the_bigger_prompt_budget(monkeypatch) -> None:
    received: dict[str, int] = {}

    class CapProbe(FakeEnricher):
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

    assert received["courtlistener-recap"] > 8000  # filings budget (36k default)
    assert received["example"] <= 8000  # news budget (8k default)


def test_enrich_prompt_carries_relevance_and_tactical_guidance() -> None:
    """The prompt must ask for the insider-threat relevance sentence, verbatim
    tool naming (the tactical TTP layer defenders search for), and multi-stack
    hunt queries — regressions here silently degrade every future enrichment."""
    from shared.llm.base import ENRICH_SYSTEM_PROMPT as p

    assert "why the case matters to an" in p and "insider-threat program" in p
    assert "digital-forensics angle" in p
    assert "name every application, service, device, or protocol" in p
    assert "Telegram" in p and "rclone" in p
    assert "Up to 4 hunt queries" in p
    assert "DIFFERENT stack" in p
    # The source-vs-inference discipline must survive the edits.
    assert "do NOT name a specific vendor, product, or log source" in p
    assert "PORTABLE pseudo-logic" in p
