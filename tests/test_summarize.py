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
    monkeypatch.setattr("shared.agents.summarize.get_summarizer_chain", lambda settings: [provider])
    monkeypatch.setattr(
        "apps.aggregator.process_pipeline.get_summarizer_provider",
        lambda settings: provider,
    )


def test_filing_needs_body_and_insider_signal() -> None:
    """Bodied filings only bill when the body itself carries an insider signal.

    The per-article itm_hits fire off docket metadata (query tags), so the
    gate scans the fetched text instead — and since 2026-08 the body signal is
    two-part: an ITM alias hit AND an insider-framing keyword. A lone alias
    passed company-v-company IP litigation (2026-08-04 audit: 58% of
    post-gate enrichments adjudicated non-insider).
    """
    from shared.agents.summarize import qualifies

    signal_body = (
        "x " * 800
    ) + "the former employee copied files to a USB drive for data exfiltration"
    alias_only_body = (
        "x " * 800
    ) + "the defendant copied plaintiff's files to a USB drive, data exfiltration alleged"
    noise_body = "y" * 2_000
    # Body + alias + framing ("former employee") → qualifies.
    assert qualifies(itm_hits=[], use_cases=[], channel="filings", text=signal_body)
    # Alias with NO framing (company-v-company class) → does NOT bill.
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text=alias_only_body)
    # Body with no insider signal at all (Valnet v. Google class) → does NOT bill.
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text=noise_body)
    # Signal-less body still qualifies when classification already vouches.
    assert qualifies(
        itm_hits=[], use_cases=["data-exfiltration"], channel="filings", text=noise_body
    )
    assert qualifies(
        itm_hits=[], use_cases=[], channel="filings", text=noise_body, itm_alignment="insider"
    )
    # A docket-entry stub → below the threshold → does not qualify.
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text="INDICTMENT")
    # News with the same empty signal never rides the filings branch.
    assert not qualifies(itm_hits=[], use_cases=[], channel="news", text=noise_body)
    # A lexical hit still qualifies for non-filings callers without a verdict.
    assert qualifies(itm_hits=["IF002"], use_cases=[], channel="news", text="")


def test_ingest_match_marker_cannot_self_qualify() -> None:
    """The lane's own match-marker line is stripped before every body check.

    The marker embeds the insider query terms that found the case, so without
    stripping, every backfilled document would inherit alias + framing from
    its own marker and the body gate would be vacuous.
    """
    from shared.agents.summarize import qualifies, strip_match_markers

    marker = "CourtListener query: former employee USB drive data exfiltration"
    # Marker + signal-free body → the marker's own terms must not qualify it.
    assert not qualifies(
        itm_hits=[], use_cases=[], channel="filings", text=f"{marker}\n" + ("y " * 800)
    )
    # Marker + genuinely signalling body → still qualifies.
    real = ("x " * 800) + "the former employee copied source code to a USB drive"
    assert qualifies(itm_hits=[], use_cases=[], channel="filings", text=f"{marker}\n{real}")
    # A stub that is ONLY the marker stays under the length floor once stripped.
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text=marker)
    # IndiaCourts markers strip the same way; unmarked text passes through.
    assert strip_match_markers("IndiaCourts match: pen drive confidential\nbody") == "body"
    assert strip_match_markers("plain body text") == "plain body text"


def test_marker_stripped_from_flattened_clean_text() -> None:
    """The gate sees clean_text, which to_plain_text FLATTENS to one line —
    line-based stripping alone is a no-op there (2026-08-22 review, verified
    by execution). The segment pass must remove the marker mid-string."""
    from shared.agents.summarize import qualifies
    from shared.utils.text import to_plain_text

    marker = "CourtListener query: former employee trade secret exfiltration"
    # The review's exact repro: title+summary+marker+signal-free boilerplate,
    # flattened the way _node_normalize builds clean_text.
    flattened = to_plain_text(
        "\n".join(["US v. Example", "Court: X\nDocket: 1", f"{marker}\n" + ("boilerplate " * 200)])
    )
    assert "\n" not in flattened  # precondition: truly flattened
    assert not qualifies(itm_hits=[], use_cases=[], channel="filings", text=flattened)
    # A genuinely signalling body (terms well past the strip window) still bills.
    real = to_plain_text(
        "\n".join(
            [
                "US v. Example",
                "Court: X\nDocket: 1",
                f"{marker}\n"
                # Well clear of the floor: the segment strip deducts ~260
                # chars from the effective body length (by design —
                # conservative), so the fixture must not sit at the boundary.
                + ("x " * 1000)
                + "the former employee copied files to a USB drive for data exfiltration",
            ]
        )
    )
    assert qualifies(itm_hits=[], use_cases=[], channel="filings", text=real)


def test_filing_stub_with_itm_hit_does_not_qualify() -> None:
    """A metadata-stub filing must NOT enrich just because it carries an ITM hit.

    CourtListener flags filings *by* the ITM-lexicon query, so nearly every
    filing alias-matches an itm_hit on its docket metadata — stub or not.
    Enriching a bodyless stub is a paid LLM call with nothing to summarize, so
    the filings branch requires the real document body regardless of itm_hits.
    """
    from shared.agents.summarize import qualifies

    # Stub body + itm_hit (and a use-case) → still skipped on the filings branch.
    assert not qualifies(
        itm_hits=["IF002"], use_cases=["data-exfiltration"], channel="filings", text="COMPLAINT"
    )
    # Same signal once the real body lands → qualifies.
    assert qualifies(
        itm_hits=["IF002"], use_cases=["data-exfiltration"], channel="filings", text="x" * 1_500
    )


def test_weak_alignment_news_does_not_spend() -> None:
    """Enrich only where extraction is plausible: weak-hit news is skipped.

    A stray alias match in a CVE roundup produces itm_hits with alignment
    "weak"; enriching it returns insider=False, methods=0 — pure spend. With
    the alignment verdict provided, only use-case or insider-aligned articles
    qualify. Callers without a verdict (None) keep the permissive behavior.
    """
    from shared.agents.summarize import qualifies

    hit = ["MT012"]
    # Weak alignment → skipped, even with a lexical hit.
    assert not qualifies(itm_hits=hit, use_cases=[], channel="news", itm_alignment="weak")
    # Insider alignment → qualifies.
    assert qualifies(itm_hits=hit, use_cases=[], channel="news", itm_alignment="insider")
    # News never bills on use-case framing alone — vendor commentary class.
    assert not qualifies(
        itm_hits=[], use_cases=["overemployment"], channel="news", itm_alignment="weak"
    )
    # Outside news (social/tips confessions) a classified use case still qualifies.
    assert qualifies(
        itm_hits=[], use_cases=["overemployment"], channel="social", itm_alignment="weak"
    )
    # No verdict (legacy/PACER callers) → any hit still qualifies.
    assert qualifies(itm_hits=hit, use_cases=[], channel="news", itm_alignment=None)


def test_backfill_skips_weak_alignment_rows(monkeypatch, tmp_path: Path) -> None:
    """The backfill sweep must not bill weak-alignment news rows."""
    from types import SimpleNamespace

    from shared.agents.summarize import article_qualifies

    weak = SimpleNamespace(
        source_id="zdnet-security",
        clean_text="CVE roundup mentioning privilege escalation once",
        use_cases=[],
        itm_alignment="weak",
        entities=SimpleNamespace(itm_hits=["MT012"]),
    )
    assert article_qualifies(weak)  # without the verdict: permissive (legacy)
    assert not article_qualifies(weak, use_itm_alignment=True)  # sweep path: skipped
    insider = SimpleNamespace(**{**weak.__dict__, "itm_alignment": "insider"})
    assert article_qualifies(insider, use_itm_alignment=True)


def test_purchase_gate_diverges_from_enrichment_gate() -> None:
    """PACER purchase-eligibility must qualify a bodyless stub the enricher skips.

    Enrichment (filing_requires_body=True) skips a stub — nothing to summarize.
    PACER purchasing (filing_requires_body=False) MUST accept the same stub —
    acquiring its body is the whole point; requiring a body first is circular.
    """
    from shared.agents.summarize import article_qualifies, qualifies

    # Bodyless filing stub carrying a docket-metadata itm_hit:
    assert not qualifies(itm_hits=["IF002"], use_cases=[], channel="filings", text="COMPLAINT")
    assert qualifies(
        itm_hits=["IF002"],
        use_cases=[],
        channel="filings",
        text="COMPLAINT",
        filing_requires_body=False,
    )
    # A stub with NO insider signal at all is bought by neither gate.
    assert not qualifies(
        itm_hits=[], use_cases=[], channel="filings", text="COMPLAINT", filing_requires_body=False
    )

    from types import SimpleNamespace

    stub = SimpleNamespace(
        source_id="courtlistener-recap",
        clean_text="COMPLAINT",
        use_cases=["data-exfiltration"],
        entities=SimpleNamespace(itm_hits=[]),
    )
    assert not article_qualifies(stub)  # enrichment skips it
    assert article_qualifies(stub, filing_requires_body=False)  # PACER buys it


def test_article_qualifies_reads_channel_and_text() -> None:
    """The backfill-path wrapper resolves channel + clean_text from the row."""
    from types import SimpleNamespace

    from shared.agents.summarize import article_qualifies

    entities = SimpleNamespace(itm_hits=[])
    full = SimpleNamespace(
        source_id="courtlistener-recap",
        clean_text=("y " * 800)
        + "former employee copied files to a USB drive for data exfiltration",
        use_cases=[],
        entities=entities,
    )
    stub = SimpleNamespace(
        source_id="courtlistener-recap", clean_text="COMPLAINT", use_cases=[], entities=entities
    )
    assert article_qualifies(full)
    assert not article_qualifies(stub)
    # Threshold is tunable, but the in-body insider-signal check still holds:
    # a bare stub stays out even at 0; a signal-bearing snippet gets in.
    assert not article_qualifies(stub, filing_min_chars=0)
    signal_stub = SimpleNamespace(
        source_id="courtlistener-recap",
        clean_text="INDICTMENT: former employee data exfiltration via USB drive",
        use_cases=[],
        entities=entities,
    )
    assert article_qualifies(signal_stub, filing_min_chars=0)
    # The 2026-08 two-part bar: alias without framing no longer bills.
    alias_only = SimpleNamespace(
        source_id="canlii-onsc",
        clean_text=("y " * 800) + "defendant company copied the database, data exfiltration",
        use_cases=[],
        entities=entities,
    )
    assert not article_qualifies(alias_only)


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
    # v3 freeze: hunt_queries left the enricher contract — even a reply
    # carrying them (this fake does) writes an empty list.
    assert forensics.hunt_queries == []
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


def test_fresh_batch_enriches_newest_filing_first(monkeypatch, tmp_path: Path) -> None:
    """The per-run enrichment budget goes to genuinely-new cases before
    force-refreshed historical filings, regardless of raw-file order."""
    from datetime import UTC, datetime

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    body = "Insider data exfiltration via removable media, trade secret theft. " * 40
    # The old filing is written FIRST (as a decade-seeding text backfill would,
    # re-stamped with a fresh ingested_at) and the new case second.
    old = _raw(
        link="https://ex.com/old-2016",
        title="Historical filing: data exfiltration case",
        channel="filings",
        content=body,
        published=datetime(2016, 1, 1, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    new = _raw(
        link="https://ex.com/new-2026",
        title="Recent filing: data exfiltration case",
        channel="filings",
        content=body,
        published=datetime(2026, 7, 20, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    JsonlArticleStore(raw_path).save([old, new])

    # One fresh-batch call, no reserve/discovery — only the first sorted case
    # gets enriched.
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "1")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    monkeypatch.setenv("DISCOVERER_MAX_ARTICLES_PER_RUN", "0")
    fake = FakeEnricher()
    _install(monkeypatch, fake)

    run_processing(raw_path=raw_path, processed_path=processed_path)

    assert fake.calls == 1
    rows = {r.link: r for r in JsonlProcessedStore(processed_path).load_all()}
    # Newest-filed case wins the single call despite appearing last in the file.
    assert rows["https://ex.com/new-2026"].forensics is not None
    assert rows["https://ex.com/old-2016"].forensics is None


def test_backfill_converts_existing_corpus_bounded_by_cap(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    # Distinct titles → distinct story_keys, so the syndication dedupe doesn't
    # collapse them: this test is about the per-run cap, not dedupe.
    JsonlArticleStore(raw_path).save(
        [
            _raw(
                link=f"https://example.com/case-{n}",
                title=f"Insider threat case {n}: data exfiltration via CVE-2024-1111{n}",
            )
            for n in range(3)
        ]
    )

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


def test_backfill_archives_generation_in_history(monkeypatch, tmp_path: Path) -> None:
    """The backfill sweep must append to enrichment_history, not just the slot."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])
    run_processing(raw_path=raw_path, processed_path=processed_path)  # floor row

    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "1")
    _install(monkeypatch, FakeEnricher())
    run_processing(raw_path=raw_path, processed_path=processed_path)  # backfill enriches

    row = JsonlProcessedStore(processed_path).load_all()[0]
    assert row.forensics is not None
    assert len(row.enrichment_history) == 1
    assert row.enrichment_history[0].ai_summary == row.ai_summary
    assert row.enrichment_history[0].forensics.model == "fake-model"


def test_zero_enrichment_tripwire_fires_when_all_calls_fail(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """Attempts>0 with 0 records ⇒ a loud [FAIL] enrichment line (dead provider)."""
    import logging as _logging

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])

    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    exploding = ExplodingEnricher()
    _install(monkeypatch, exploding)
    with caplog.at_level(_logging.ERROR, logger="apps.aggregator.process_pipeline"):
        result = run_processing(raw_path=raw_path, processed_path=processed_path)

    assert exploding.calls >= 1
    assert result.enrich_attempts >= 1 and result.enrich_saved == 0
    assert any("[FAIL] enrichment" in rec.message for rec in caplog.records)


def test_zero_enrichment_tripwire_quiet_on_success_and_noop(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """No [FAIL] line when enrichment works, or when no attempts were made."""
    import logging as _logging

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])

    # Working provider → attempts == saved, no tripwire.
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "5")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "0")
    _install(monkeypatch, FakeEnricher())
    with caplog.at_level(_logging.ERROR, logger="apps.aggregator.process_pipeline"):
        result = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert result.enrich_saved >= 1
    assert not any("[FAIL] enrichment" in rec.message for rec in caplog.records)

    # Provider off → zero attempts, still no tripwire.
    caplog.clear()
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "0")
    with caplog.at_level(_logging.ERROR, logger="apps.aggregator.process_pipeline"):
        result = run_processing(raw_path=raw_path, processed_path=processed_path, force=True)
    assert result.enrich_attempts == 0
    assert not any("[FAIL] enrichment" in rec.message for rec in caplog.records)


def test_backfill_dedupes_syndicated_story(monkeypatch, tmp_path: Path) -> None:
    """Same title+day under three domains = one story = one enrichment bill."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            _raw(link=f"https://outlet-{n}.example.com/story", source_id=f"outlet-{n}")
            for n in range(3)
        ]
    )
    # Rows land un-enriched (no provider), all sharing one story_key.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    rows = JsonlProcessedStore(processed_path).load_all()
    assert len({r.story_key for r in rows}) == 1

    fake = FakeEnricher()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1  # one sibling billed, not three
    rows = JsonlProcessedStore(processed_path).load_all()
    assert sum(1 for r in rows if r.forensics is not None) == 1

    # Siblings of an enriched story are never billed on later runs either.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1


def test_fresh_ingest_dedupes_syndicated_story(monkeypatch, tmp_path: Path) -> None:
    """Syndicated siblings arriving in one ingest bill once in the main loop."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save(
        [
            _raw(link=f"https://outlet-{n}.example.com/story", source_id=f"outlet-{n}")
            for n in range(3)
        ]
    )
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1
    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 3  # siblings still land as floor rows
    assert sum(1 for r in rows if r.forensics is not None) == 1


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


def _clear_selected(row):
    """Simulate courtlistener_pipeline._clear_llm_fields: drop the selected
    pointer + llm hits, PRESERVE enrichment_history (the archive)."""
    hits = [h for h in row.entities.itm_hits if getattr(h, "source", "lexical") != "llm"]
    return row.model_copy(
        update={
            "ai_summary": None,
            "case_record": None,
            "forensics": None,
            "entities": row.entities.model_copy(update={"itm_hits": hits}),
        }
    )


def test_enrichment_history_records_first_generation(monkeypatch) -> None:
    """The first enrichment is archived as generation #1, mirrored by the view."""
    _install(monkeypatch, FakeEnricher())
    processed = process_article(_raw())
    assert len(processed.enrichment_history) == 1
    gen = processed.enrichment_history[0]
    # The selected view equals the sole stored generation.
    assert gen.ai_summary == processed.ai_summary
    assert gen.forensics.methods[0].action == processed.forensics.methods[0].action
    assert gen.forensics.model == "fake-model"


def test_thin_reenrich_appends_but_never_guts(monkeypatch) -> None:
    """A thinner re-enrichment is stored but the richer prior stays selected."""
    _install(monkeypatch, FakeEnricher())
    rich = process_article(_raw())
    assert rich.forensics is not None and rich.forensics.methods

    # Clear the selected pointer (history preserved) and re-enrich thinly.
    cleared = _clear_selected(rich)
    thin = FakeEnricher(
        _reply(ai_summary="second, thinner pass", methods=[], confidence=0.2, hunt_queries=[])
    )
    _install(monkeypatch, thin)
    reprocessed = process_article(_raw(), prior=cleared)

    assert thin.calls == 1  # it DID re-bill (cleared → not a cache hit)
    # Both generations are archived …
    assert len(reprocessed.enrichment_history) == 2
    # … but the richer first generation stays selected — the card is not gutted.
    assert reprocessed.ai_summary == rich.ai_summary
    assert reprocessed.forensics is not None and reprocessed.forensics.methods
    # The thin generation is present in history but not selected.
    notes = {g.ai_summary for g in reprocessed.enrichment_history}
    assert "second, thinner pass" in notes


def test_reenrich_dedup_skips_identical_generation(monkeypatch) -> None:
    """Re-running the same model over the same text does not bloat history."""
    _install(monkeypatch, FakeEnricher())
    rich = process_article(_raw())
    cleared = _clear_selected(rich)

    same = FakeEnricher()  # identical default reply → identical signature
    _install(monkeypatch, same)
    reprocessed = process_article(_raw(), prior=cleared)

    assert same.calls == 1  # the LLM was called …
    assert len(reprocessed.enrichment_history) == 1  # … but the dup was not stored
    assert reprocessed.forensics is not None and reprocessed.forensics.methods


def test_legacy_row_seeds_history_on_reprocess(monkeypatch) -> None:
    """A pre-versioning row (forensics, no history) seeds history on reprocess."""
    _install(monkeypatch, FakeEnricher())
    rich = process_article(_raw())
    legacy = rich.model_copy(update={"enrichment_history": []})  # pre-feature shape
    assert legacy.forensics is not None and legacy.enrichment_history == []

    fake = FakeEnricher()
    _install(monkeypatch, fake)
    reprocessed = process_article(_raw(), prior=legacy)

    assert fake.calls == 0  # carried-forward record is reused, never re-billed
    assert len(reprocessed.enrichment_history) == 1  # seeded from the legacy record
    assert reprocessed.forensics is not None and reprocessed.forensics.methods


def test_legacy_case_record_only_row_is_preserved_not_wiped(monkeypatch) -> None:
    """A pre-forensics row (case_record, no forensics/history) survives reprocess.

    The select-best projection must never gut a legacy row that has nothing to
    version — reprocessing with the enricher off keeps its case_record intact.
    """
    _install(monkeypatch, FakeEnricher())
    base = process_article(_raw())
    legacy = base.model_copy(
        update={
            "forensics": None,
            "enrichment_history": [],
            "ai_summary": "legacy analyst note",
            "case_record": CaseRecord(is_insider_case=True, methods=["legacy method"]),
        }
    )

    fake = FakeEnricher()
    _install(monkeypatch, fake)
    monkeypatch.setenv("SUMMARIZER_UPGRADE_LEGACY", "0")
    reprocessed = process_article(_raw(), prior=legacy)

    assert fake.calls == 0  # not re-billed
    assert reprocessed.ai_summary == "legacy analyst note"  # NOT wiped
    assert reprocessed.case_record is not None
    assert reprocessed.case_record.methods == ["legacy method"]
    assert reprocessed.forensics is None and reprocessed.enrichment_history == []


def test_case_record_sanitization_clamps() -> None:
    record = CaseRecord(
        actor_role="x" * 500,
        methods=[f"method {i}\x00\x01" for i in range(20)],
        motive_signals=["dup", "DUP", "  dup  "],
    )
    clean = record.sanitized()
    assert len(clean.actor_role) == 200  # short label field stays tight
    assert len(clean.methods) == 12  # methods can be many on rich filings
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
    # v3 freeze pins (docs/schema-freeze-v3.md): the five prompt-contract
    # changes must survive future edits.
    assert "VERBATIM OR EMPTY" in p and "machine-checked" in p
    assert "Calibrate to these bands" in p and "a complaint is never 0.95" in p
    assert "every route data LEFT BY" in p
    assert "A name is NEVER evidence of nationality" in p
    assert "a fillable field is never a reason to call" in p
    assert "Insider Threat Matrix 2.12" in p
    assert "caught (it detected or stopped the conduct)" in p
    assert "financial-services" in p
    assert "hunt_queries" not in p  # dead weight, out of the contract
    # The source-vs-inference discipline must survive the edits.
    assert "do NOT name a specific vendor, product, or log source" in p
    # Per-field fill demands: schema-literal models (Qwen) emit the specimen's
    # null/[] defaults unless each case fact is explicitly demanded — corpus
    # audit 2026-08-19: detection filled 3% by Qwen vs 49% by Haiku.
    assert "fill EVERY one of these the text establishes" in p
    assert "SOURCE IS SILENT" in p
    assert "HOW the conduct came to light" in p
    # (v3 reworded the exfil demand; the LEFT BY pin above covers it)


def test_backfill_prioritizes_filings_over_newer_news(monkeypatch, tmp_path: Path) -> None:
    """A filing's `published` is its (often historical) filing date, so pure
    recency would starve court cases behind every fresh news day."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    body = "The defendant exfiltrated trade secret schematics to a personal drive. " * 40
    filing = _raw(
        title="United States v. Example",
        link="https://www.courtlistener.com/docket/9/us-v-example/",
        summary="Court: SDNY",
        content=f"CourtListener query: q\n{body}",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
        published="2024-01-01T00:00:00Z",
    )
    news = _raw(link="https://example.com/newer-news", published="2026-07-01T00:00:00Z")
    JsonlArticleStore(raw_path).save([filing, news])

    # Pre-feature corpus: rows exist, nothing enriched.
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert all(r.forensics is None for r in JsonlProcessedStore(processed_path).load_all())

    # One-call budget: the older filing must win over the newer news row.
    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "1")
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)
    assert fake.calls == 1
    rows = {r.link: r for r in JsonlProcessedStore(processed_path).load_all()}
    assert rows["https://www.courtlistener.com/docket/9/us-v-example/"].forensics is not None
    assert rows["https://example.com/newer-news"].forensics is None


def test_backfill_reserve_survives_heavy_news_day(monkeypatch, tmp_path: Path) -> None:
    """The fresh-ingest batch must not eat the whole budget: the reserved
    slice guarantees the stored court-case backlog keeps converting."""
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    body = "The defendant exfiltrated trade secret schematics to a personal drive. " * 40
    filing = _raw(
        title="United States v. Example",
        link="https://www.courtlistener.com/docket/9/us-v-example/",
        summary="Court: SDNY",
        content=f"CourtListener query: q\n{body}",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
        published="2024-01-01T00:00:00Z",
    )
    JsonlArticleStore(raw_path).save([filing])
    run_processing(raw_path=raw_path, processed_path=processed_path)  # no provider yet

    # A pile of fresh news arrives; without the reserve it would drain the cap.
    news = [
        _raw(link=f"https://example.com/news-{n}", published="2026-07-01T00:00:00Z")
        for n in range(5)
    ]
    JsonlArticleStore(raw_path).save(news)

    monkeypatch.setenv("SUMMARIZER_MAX_ARTICLES_PER_RUN", "2")
    monkeypatch.setenv("SUMMARIZER_BACKFILL_RESERVE", "1")
    fake = FakeEnricher()
    _install(monkeypatch, fake)
    run_processing(raw_path=raw_path, processed_path=processed_path)

    assert fake.calls == 2  # total spend still honors the cap
    rows = {r.link: r for r in JsonlProcessedStore(processed_path).load_all()}
    # The reserved slice converted the stored filing despite the news flood.
    assert rows["https://www.courtlistener.com/docket/9/us-v-example/"].forensics is not None
    news_enriched = sum(1 for link, r in rows.items() if "news-" in link and r.forensics)
    assert news_enriched == 1  # main batch was capped at cap - reserve

def test_context_kind_parsed_and_validated():
    from shared.schemas.forensics import parse_forensics_json

    rec = parse_forensics_json(
        {"is_insider_case": False, "context_kind": " Detection "},
        link="http://x", title="t",
    )
    assert rec.context_kind == "detection"
    junk = parse_forensics_json(
        {"is_insider_case": False, "context_kind": "totally-made-up"},
        link="http://x", title="t",
    )
    assert junk.context_kind == ""
