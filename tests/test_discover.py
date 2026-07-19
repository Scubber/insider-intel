"""Tests for the novel-technique discovery pass (second LLM call at ingest)."""

from __future__ import annotations

from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.articles import CaseRecord
from shared.schemas.discovery import derive_evidence_strength, parse_discovery_json
from shared.schemas.forensics import CaseMethod, CaseObservable, PerCaseForensics


def _raw(**overrides) -> RawArticle:
    data = {
        "title": "US v. Example — insider data theft",
        "link": "https://example.com/case-a",
        "summary": "Departing engineer exfiltrated trade secrets.",
        "content": "x" * 2000,
        "source_id": "courtlistener-recap",
        "source_name": "Court",
    }
    data.update(overrides)
    return RawArticle.model_validate(data)


def _forensics(title: str = "US v. Example") -> PerCaseForensics:
    return PerCaseForensics(
        link="",
        title=title,
        is_insider_case=True,
        methods=[
            CaseMethod(
                action="synced 9,000 CAD files to a personal Dropbox via rclone",
                tools=["rclone"],
                claim_status="adjudicated",
                observables=[
                    CaseObservable(description="bulk egress", basis="mechanically_implied")
                ],
            )
        ],
        candidate_technique_ids=[],
    )


def _assessment(**overrides) -> dict:
    data = {
        "method_index": 0,
        "disposition": "novel",
        "mapped_itm_id": None,
        "novel": {
            "label": "rclone bulk cloud sync",
            "portable_behavior": "insider uses a CLI cloud-sync tool to bulk-exfiltrate data",
            "case_specific_procedure": "rclone to Dropbox",
            "distinctness_rationale": "CLI-driven sync not covered by a shortlisted technique",
        },
    }
    data.update(overrides)
    return data


class FakeDiscoverer:
    model_name = "fake-discoverer"

    def __init__(self, reply: dict | None = None) -> None:
        self.calls = 0
        self.reply = reply if reply is not None else {"assessments": [_assessment()]}

    def discover_techniques(self, *, forensics_json, itm_shortlist):
        self.calls += 1
        return self.reply


class ExplodingDiscoverer(FakeDiscoverer):
    def discover_techniques(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider down")


def _fake_enrich(title: str):
    def _enrich(**kw):
        f = _forensics(title=kw.get("title") or title)
        return "note", f, CaseRecord(is_insider_case=True), []

    return _enrich


def _install(monkeypatch, discoverer, *, enrich_title="US v. Example") -> None:
    monkeypatch.setattr("shared.agents.article_processor.enrich_fields", _fake_enrich(enrich_title))
    monkeypatch.setattr(
        "shared.agents.discover.get_discoverer_provider", lambda settings: discoverer
    )


def test_provider_unset_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr("shared.agents.article_processor.enrich_fields", _fake_enrich("t"))
    monkeypatch.setattr("shared.agents.discover.get_discoverer_provider", lambda settings: None)
    processed = process_article(_raw())
    assert processed.forensics is not None  # enriched
    assert processed.discovery is None  # but no discovery provider


def test_qualifying_case_gets_discovery(monkeypatch) -> None:
    fake = FakeDiscoverer()
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    disc = processed.discovery
    assert disc is not None
    assert disc.model == "fake-discoverer"
    novel = disc.novel_assessments()
    assert novel and novel[0].novel.label == "rclone bulk cloud sync"
    # evidence_strength derived from the method (adjudicated + mechanically_implied).
    assert novel[0].evidence_strength == "strong"


def test_exploding_provider_degrades_to_none(monkeypatch) -> None:
    fake = ExplodingDiscoverer()
    _install(monkeypatch, fake)
    processed = process_article(_raw())
    assert fake.calls == 1
    assert processed.forensics is not None  # enrichment unaffected
    assert processed.discovery is None  # discovery failed softly


def test_non_insider_case_never_calls_discoverer(monkeypatch) -> None:
    def _enrich(**kw):
        f = _forensics()
        f = f.model_copy(update={"is_insider_case": False})
        return "note", f, CaseRecord(is_insider_case=False), []

    fake = FakeDiscoverer()
    monkeypatch.setattr("shared.agents.article_processor.enrich_fields", _enrich)
    monkeypatch.setattr("shared.agents.discover.get_discoverer_provider", lambda settings: fake)
    processed = process_article(_raw())
    assert fake.calls == 0
    assert processed.discovery is None


def test_carry_forward_never_rebills(monkeypatch) -> None:
    fake = FakeDiscoverer()
    _install(monkeypatch, fake)
    first = process_article(_raw())
    assert fake.calls == 1
    # Reprocess carrying the prior discovery → the provider is not called again.
    second = process_article(_raw(), prior=first)
    assert fake.calls == 1
    assert second.discovery is not None
    assert second.discovery.novel_assessments()[0].novel.label == "rclone bulk cloud sync"


def test_derive_evidence_strength_table() -> None:
    strong = CaseMethod(
        action="a",
        claim_status="adjudicated",
        observables=[CaseObservable(description="d", basis="mechanically_implied")],
    )
    weak = CaseMethod(
        action="a",
        claim_status="alleged",
        observables=[CaseObservable(description="d", basis="analyst_inference")],
    )
    weak_no_obs = CaseMethod(action="a", claim_status="unclear")
    moderate = CaseMethod(action="a", claim_status="reported")
    assert derive_evidence_strength(strong) == "strong"
    assert derive_evidence_strength(weak) == "weak"
    assert derive_evidence_strength(weak_no_obs) == "weak"
    assert derive_evidence_strength(moderate) == "moderate"


def test_parse_discovery_json_coerces_and_never_raises() -> None:
    forensics = _forensics()
    disc = parse_discovery_json(
        {
            "assessments": [
                _assessment(),  # valid novel
                {
                    "method_index": 99,
                    "disposition": "novel",
                    "novel": {"label": "oob"},
                },  # out of range
                {
                    "method_index": 0,
                    "disposition": "mapped",
                    "mapped_itm_id": "ZZ999",
                },  # unknown id
                "junk",
            ]
        },
        forensics=forensics,
        link="l",
        title="t",
    )
    # Only the valid novel assessment survives; oob + unknown-id + junk drop.
    assert len(disc.assessments) == 1
    assert disc.assessments[0].disposition == "novel"
    # Malformed top-level input never raises.
    assert parse_discovery_json("nope", forensics=forensics, link="l", title="t").assessments == []
