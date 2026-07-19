"""Tests for the novel-candidate aggregation (clustering + lifecycle status)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.technique_seeds import (
    ITM_DISTINCTNESS_THRESHOLD,
    TechniqueSeedStore,
    rebuild_technique_seeds,
)
from shared.schemas import ProcessedArticle
from shared.schemas.discovery import CaseDiscovery, MethodAssessment, NovelBehavior
from shared.schemas.forensics import CaseMethod, PerCaseForensics

# A behavior phrased far from any ITM technique description, so it reads as
# clearly distinct (max ITM cosine below the distinctness threshold).
_DISTINCT_BEHAVIOR = (
    "zzqx rclone bulk cloud sync exfiltration wombat quokka predeparture trove"
)


def _article(
    link: str,
    *,
    story_key: str,
    claim_status: str = "adjudicated",
    evidence: str = "strong",
    behavior: str = _DISTINCT_BEHAVIOR,
    published: datetime | None = None,
) -> ProcessedArticle:
    forensics = PerCaseForensics(
        link=link,
        title=f"Case {link}",
        is_insider_case=True,
        methods=[CaseMethod(action="synced files via rclone", claim_status=claim_status)],
    )
    discovery = CaseDiscovery(
        link=link,
        title=f"Case {link}",
        assessments=[
            MethodAssessment(
                method_index=0,
                disposition="novel",
                novel=NovelBehavior(label=behavior, portable_behavior=behavior),
                evidence_strength=evidence,  # type: ignore[arg-type]
            )
        ],
    )
    return ProcessedArticle(
        title=f"Case {link}",
        link=link,
        published=published or datetime(2026, 1, 1, tzinfo=UTC),
        source_id="courtlistener-recap",
        source_name="Court",
        clean_text="x",
        story_key=story_key,
        forensics=forensics,
        discovery=discovery,
    )


def _rebuild(tmp_path, articles) -> list:
    pstore = JsonlProcessedStore(tmp_path / "processed.jsonl")
    pstore.save(articles)
    store = TechniqueSeedStore(tmp_path / "seeds.json")
    rebuild_technique_seeds(pstore, store=store)
    return store.read().candidates


def test_two_distinct_incidents_corroborate_to_eligible(tmp_path) -> None:
    cands = _rebuild(
        tmp_path,
        [
            _article("https://a.com/1", story_key="sk1"),
            _article("https://b.com/2", story_key="sk2"),
        ],
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.corroboration_count == 2
    assert c.max_itm_similarity < ITM_DISTINCTNESS_THRESHOLD  # clearly distinct
    assert c.status == "eligible"
    assert c.flagged_for_review is True
    assert c.id.startswith("NOVEL-")


def test_same_incident_two_outlets_counts_once(tmp_path) -> None:
    # Same story_key across two domains → one incident → not corroborated.
    cands = _rebuild(
        tmp_path,
        [
            _article("https://a.com/1", story_key="same"),
            _article("https://b.com/2", story_key="same"),
        ],
    )
    assert len(cands) == 1
    assert cands[0].corroboration_count == 1
    assert cands[0].distinct_domains == 2
    assert cands[0].status == "seed"


def test_evidence_gate_caps_weak_evidence_at_seed(tmp_path) -> None:
    # Two distinct incidents, but allegation-only + inference-only evidence.
    cands = _rebuild(
        tmp_path,
        [
            _article("https://a.com/1", story_key="sk1", claim_status="alleged", evidence="weak"),
            _article("https://b.com/2", story_key="sk2", claim_status="alleged", evidence="weak"),
        ],
    )
    assert len(cands) == 1
    assert cands[0].corroboration_count == 2  # corroborated by incident count
    assert cands[0].status == "seed"  # but gated down by weak evidence


def test_single_case_is_seed(tmp_path) -> None:
    cands = _rebuild(tmp_path, [_article("https://a.com/1", story_key="sk1")])
    assert len(cands) == 1
    assert cands[0].status == "seed"
    assert cands[0].corroboration_count == 1


def test_store_round_trip_and_determinism(tmp_path) -> None:
    articles = [
        _article("https://a.com/1", story_key="sk1"),
        _article("https://b.com/2", story_key="sk2"),
    ]
    pstore = JsonlProcessedStore(tmp_path / "processed.jsonl")
    pstore.save(articles)
    store = TechniqueSeedStore(tmp_path / "seeds.json")

    rebuild_technique_seeds(pstore, store=store)
    first = store.path.read_text()
    rebuild_technique_seeds(pstore, store=store)
    second = store.path.read_text()
    assert first == second  # deterministic over the same corpus

    resp = store.read()
    assert resp.candidate_count == 1
    assert resp.counts_by_status.get("eligible") == 1


def test_missing_store_reads_empty(tmp_path) -> None:
    store = TechniqueSeedStore(tmp_path / "does-not-exist.json")
    resp = store.read()
    assert resp.candidate_count == 0
    assert resp.candidates == []
