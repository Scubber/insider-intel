"""Tests for the pure-assembly hunt report (stored forensics → report, no LLM)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service, ttp_extract
from shared.agents import process_article
from shared.llm.base import pack_case_text
from shared.schemas import RawArticle
from shared.schemas.forensics import (
    CaseMethod,
    CaseObservable,
    HuntQuerySeed,
    PerCaseForensics,
    case_record_from_forensics,
    parse_forensics_json,
)
from shared.settings import Settings


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


def _forensics(link: str, title: str) -> PerCaseForensics:
    return PerCaseForensics(
        link=link,
        title=title,
        is_insider_case=True,
        source_type="court_filing",
        legal_posture="indictment",
        actor_profile="departing engineer — repo access",
        methods=[
            CaseMethod(
                action="synced 9,700 design files to a personal Dropbox",
                tools=["Dropbox"],
                quantity="9,700 files",
                claim_status="alleged",
                evidence_quote="synced roughly 9,700 files to a personal Dropbox",
                observables=[
                    CaseObservable(
                        description="Bulk uploads to dropbox.com",
                        artifact="proxy/egress logs",
                        channel="cloud",
                        basis="analyst_inference",
                    )
                ],
            )
        ],
        detection="forensic review of the returned laptop",
        candidate_technique_ids=["IF002"],
        hunt_terms=["dropbox.com/home"],
        hunt_queries=[
            HuntQuerySeed(
                stack="Splunk/SIEM",
                logic="index=proxy dest_domain=dropbox.com bytes_out>100MB",
                rationale="bulk sync pattern",
            )
        ],
        extraction_status="llm",
    )


def _index_with_forensics(tmp_path, article, forensics):
    enriched = article.model_copy(update={"forensics": forensics})
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([enriched])
    return service.get_index(path, reload=True)


def test_report_assembles_from_stored_forensics(tmp_path) -> None:
    article = _article("US v. Example", "https://example.com/case-a")
    forensics = _forensics(article.link, article.title)
    index = _index_with_forensics(tmp_path, article, forensics)

    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=_cfg())
    assert report.mode == "llm"  # an enriched record contributed
    assert report.report_version == 3
    assert "Assembled from stored forensics · 1 enriched / 0 floor" in report.detail

    section = next(s for s in report.techniques if s.id == "IF002")
    # Bullets come from the stored method actions.
    assert section.cases[0].bullets == ["synced 9,700 design files to a personal Dropbox"]
    # Legal posture rides onto the case evidence for the UI badge.
    assert section.cases[0].legal_posture == "indictment"
    assert section.observables and section.observables[0].channel == "cloud"
    # The analyst-inference basis survives assembly into the section.
    assert section.observables[0].basis == "analyst_inference"
    # Hunt queries precomputed at ingest surface on the section.
    assert section.detection.hunt_queries[0].logic.startswith("index=proxy")

    # DT*/PV* controls attach from the catalog in code.
    from shared.itm.index import load_itm_index

    tech = next(t for t in load_itm_index().techniques if t.id == "IF002")
    assert [c.id for c in section.detection.detections] == sorted({r.id for r in tech.detections})
    assert section.theme == tech.theme

    # Legacy cue fields derive from the new structure (cloud → network bucket).
    assert any("Bulk uploads" in cue for cue in report.network)
    assert "dropbox.com/home" in report.seeds


def test_report_falls_back_to_floor_for_unenriched(tmp_path) -> None:
    # No stored forensics → floor-derived record; still a report, mode "seeds".
    article = _article(
        "Insider threat: engineer exfiltrated trade secrets", "https://example.com/case-floor"
    )
    assert article.entities.itm_hits and article.forensics is None
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([article])
    index = service.get_index(path, reload=True)

    report = ttp_extract.extract_ttps_for_links(index, [article.link], settings=_cfg())
    assert report.mode == "seeds"
    assert "0 enriched / 1 floor" in report.detail
    # The lexically-matched technique still has a section (from the floor).
    assert {s.id for s in report.techniques} & {h.id.upper() for h in article.entities.itm_hits}


def test_mixed_board_counts_enriched_and_floor(tmp_path) -> None:
    a = _article("US v. Alpha", "https://example.com/alpha")
    b = _article("US v. Bravo", "https://example.com/bravo")
    enriched = a.model_copy(update={"forensics": _forensics(a.link, a.title)})
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([enriched, b])
    index = service.get_index(path, reload=True)

    report = ttp_extract.extract_ttps_for_links(index, [a.link, b.link], settings=_cfg())
    assert report.mode == "llm"
    assert "1 enriched / 1 floor" in report.detail


def test_unindexed_links_short_circuit(tmp_path) -> None:
    index = service.get_index(tmp_path / "empty.jsonl", reload=True)
    report = ttp_extract.extract_ttps_for_links(
        index, ["https://example.invalid/x"], settings=_cfg()
    )
    assert report.mode == "seeds"
    assert report.article_count == 0
    assert report.detail == "No indexed articles matched board links"


def test_parse_forensics_json_coerces_and_never_raises() -> None:
    # Wrong types everywhere — coercion drops bad fields, never raises.
    f = parse_forensics_json(
        {
            "actor_profile": 42,
            "source_type": "tabloid",  # not allowed → unknown
            "legal_posture": "vibes",  # not allowed → unknown
            "methods": [
                {"action": 1},
                "nope",
                {
                    "action": "ok",
                    "observables": "bad",
                    "claim_status": "definitely",  # invalid → unclear
                    "evidence_quote": 99,  # non-str → ""
                },
            ],
            "hunt_queries": [{"logic": "index=x"}, "junk"],
            "is_insider_case": "yes",
            "confidence": "high",
            "exfil_channels": [None, "USB"],
        },
        link="l",
        title="t",
    )
    assert [m.action for m in f.methods] == ["ok"]
    assert f.methods[0].observables == []  # bad observables dropped
    assert f.methods[0].claim_status == "unclear"  # invalid enum → default
    assert f.methods[0].evidence_quote == ""  # non-str → default
    assert f.source_type == "unknown"  # invalid enum → default
    assert f.legal_posture == "unknown"  # invalid enum → default
    assert f.hunt_queries[0].logic == "index=x"
    assert f.confidence == 0.0  # non-numeric coerced
    assert f.exfil_channels == ["USB"]


def test_forensics_from_floor_reshapes_case_record() -> None:
    from shared.schemas import CaseRecord

    article = _article("US v. Floor", "https://example.com/from-floor").model_copy(
        update={
            "case_record": CaseRecord(
                is_insider_case=True,
                actor_role="contractor sysadmin",
                methods=["dumped the customer database"],
                exfil_channels=["personal Gmail"],
                detection_trigger="DLP alert",
            )
        }
    )
    record = ttp_extract.forensics_from_floor(article)
    assert record.extraction_status == "floor"
    assert "contractor sysadmin" in record.actor_profile
    actions = [m.action for m in record.methods]
    assert "dumped the customer database" in actions
    assert any("personal Gmail" in a for a in actions)
    assert record.detection == "DLP alert"


def test_case_record_from_forensics_derives_and_sanitizes() -> None:
    f = PerCaseForensics(
        link="l",
        title="t",
        is_insider_case=True,
        actor_profile="departing engineer — repo access",
        methods=[CaseMethod(action="x" * 500)],
        exfil_channels=["personal Dropbox"],
        detection="laptop review",
    )
    record = case_record_from_forensics(f)
    assert record.is_insider_case
    assert record.actor_role == "departing engineer"  # from actor_profile head
    assert record.detection_trigger == "laptop review"
    assert record.exfil_channels == ["personal Dropbox"]
    # Method actions are full sentences, not labels — kept at the 600-char bound.
    assert len(record.methods[0]) == 500  # the 500-char action survives intact


def test_narrative_fields_survive_past_200_chars() -> None:
    """DETECTED VIA / OUTCOME are full sentences — must not clip at 200 chars."""
    long_detection = "Forensic analysis of the corpus " + ("x" * 400)
    f = PerCaseForensics(
        link="l",
        title="t",
        is_insider_case=True,
        detection=long_detection,
        outcome="y" * 2500,
    )
    record = case_record_from_forensics(f)
    # Survives well past the 200-char label clamp; capped only at the 2000 bound.
    assert len(record.detection_trigger) == len(long_detection)
    assert len(record.outcome) == 2000


def test_pack_case_text_filing_head_and_tail() -> None:
    body = "The defendant exfiltrated schematics. " * 1200  # ~46k chars
    packed = pack_case_text(body, max_chars=36_000, is_filing=True)
    assert len(packed) <= 36_000 + 50
    assert "…[middle truncated]…" in packed
    assert packed.rstrip().endswith("The defendant exfiltrated schematics.")
    news = pack_case_text(body, max_chars=8_000, is_filing=False)
    assert len(news) == 8_000
    assert "…[middle truncated]…" not in news
