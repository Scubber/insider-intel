"""Hunt synthesis: signature cache, budget cap, tolerant parsing, store round-trip."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.aggregator.hunt_synthesis import (
    MIN_CASES_FOR_SYNTHESIS,
    TechniqueHuntStore,
    material_signature,
    run_hunt_synthesis,
    technique_material,
)
from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import CaseMethod, HuntQuerySeed, PerCaseForensics
from shared.schemas.hunt_patterns import parse_patterns
from shared.settings import Settings

NOW = datetime.now(UTC)

GOOD_REPLY = {
    "patterns": [
        {
            "name": "Departure-window bulk copy",
            "who_class": "departing employees",
            "action": "bulk copy of sensitive files to removable media before exit",
            "target_class": "source code / design files",
            "channel": "endpoint",
            "logic": "FROM <edr_removable_media_log> WHERE user IN <departing_users> "
            "AND file_count > <threshold>",
            "log_sources": ["EDR removable-media events"],
            "thresholds": "scope to 30 days around resignation",
            "false_positives": "IT asset-migration jobs",
        },
        {"name": "no logic — dropped"},
        "not-a-dict",
    ]
}


class FakeSynthesizer:
    model_name = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize_hunts(self, *, technique_json: str) -> dict | None:
        self.calls += 1
        assert "Holly Hill" not in technique_json  # entity filter feeds synthesis
        return GOOD_REPLY


def _seed_corpus(tmp_path, n: int = 2):
    forensics = PerCaseForensics(
        link="x",
        title="x",
        candidate_technique_ids=["IF002"],
        methods=[CaseMethod(action="USB copy of design files", claim_status="adjudicated")],
        hunt_terms=["removable media", "Holly Hill Logistics"],
        hunt_queries=[HuntQuerySeed(stack="EDR", logic="removable_media_write > 100")],
        is_insider_case=True,
    )
    processed = []
    for i in range(n):
        raw = RawArticle(
            title=f"Case {i}",
            link=f"https://ex.com/{i}",
            summary="Departing employee copied files to USB.",
            content="Insider exfiltration via removable media.",
            published=NOW,
            source_id="example",
            source_name="Example",
        )
        art = process_article(raw)
        processed.append(
            art.model_copy(update={"forensics": forensics.model_copy(update={"link": art.link})})
        )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(processed)
    return path


def _settings(tmp_path, **kw) -> Settings:
    return Settings(
        PROCESSED_ARTICLES_PATH=str(tmp_path / "processed.jsonl"),
        TECHNIQUE_HUNTS_PATH=str(tmp_path / "hunts.json"),
        SUMMARIZER_LLM_PROVIDER="anthropic",
        **kw,
    )


def test_parse_patterns_tolerant() -> None:
    patterns = parse_patterns(GOOD_REPLY)
    assert len(patterns) == 1
    assert patterns[0].channel == "endpoint" and patterns[0].who_class == "departing employees"
    assert parse_patterns({"patterns": "nope"}) == []
    assert parse_patterns(None) == []


def test_synthesis_generates_caches_and_respects_budget(tmp_path, monkeypatch) -> None:
    path = _seed_corpus(tmp_path)
    settings = _settings(tmp_path)
    fake = FakeSynthesizer()
    monkeypatch.setattr(
        "apps.aggregator.hunt_synthesis.get_synthesizer_chain", lambda _s: [fake]
    )

    # Dry-run counts but never calls.
    dry = run_hunt_synthesis(path, settings=settings, dry_run=True)
    assert dry.stale == 1 and dry.stale_ids == ["IF002"] and fake.calls == 0

    result = run_hunt_synthesis(path, settings=settings)
    assert result.generated == 1 and fake.calls == 1
    entry = TechniqueHuntStore(settings.technique_hunts_path).read()["IF002"]
    assert entry.model == "fake-model" and entry.case_count == 2
    assert entry.patterns[0].name == "Departure-window bulk copy"

    # Unchanged material → cached, no second call.
    again = run_hunt_synthesis(path, settings=settings)
    assert again.cached == 1 and again.generated == 0 and fake.calls == 1

    # Budget 0 disables the pass entirely.
    zero = run_hunt_synthesis(
        path, settings=_settings(tmp_path, HUNT_SYNTH_MAX_PER_RUN=0), dry_run=False
    )
    assert zero.generated == 0


def test_signature_changes_with_material(tmp_path) -> None:
    path = _seed_corpus(tmp_path)
    settings = _settings(tmp_path)
    from shared.utils.evidence import build_evidence_ledger

    rows = [
        {
            "link": a.link,
            "title": a.title,
            "published": a.published.isoformat() if a.published else "",
            "forensics": a.forensics.model_dump(mode="json") if a.forensics else None,
        }
        for a in JsonlProcessedStore(path).load_all()
    ]
    ledger = build_evidence_ledger(rows, top=1)
    m1 = technique_material(ledger, "IF002")
    assert m1["cases"] == 2 >= MIN_CASES_FOR_SYNTHESIS
    # Entity terms never reach the synthesis input.
    assert "Holly Hill Logistics" not in m1["generic_indicators"]
    sig1 = material_signature(m1)
    m2 = dict(m1, cases=3)
    assert material_signature(m2) != sig1
    assert settings.hunt_synth_max_per_run == 10


def test_below_min_cases_not_eligible(tmp_path, monkeypatch) -> None:
    path = _seed_corpus(tmp_path, n=1)
    fake = FakeSynthesizer()
    monkeypatch.setattr(
        "apps.aggregator.hunt_synthesis.get_synthesizer_chain", lambda _s: [fake]
    )
    result = run_hunt_synthesis(path, settings=_settings(tmp_path))
    assert result.eligible == 0 and fake.calls == 0


def test_failed_synthesis_counts_and_keeps_store(tmp_path, monkeypatch) -> None:
    path = _seed_corpus(tmp_path)
    settings = _settings(tmp_path)

    class DeadProvider:
        model_name = "dead"

        def synthesize_hunts(self, *, technique_json: str) -> dict | None:
            return None

    monkeypatch.setattr(
        "apps.aggregator.hunt_synthesis.get_synthesizer_chain", lambda _s: [DeadProvider()]
    )
    result = run_hunt_synthesis(path, settings=settings)
    assert result.failed == 1 and result.generated == 0
    assert TechniqueHuntStore(settings.technique_hunts_path).read() == {}
