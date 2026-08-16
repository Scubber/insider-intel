"""Tests for the LangGraph article processor."""

from __future__ import annotations

from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
from shared.schemas import RawArticle


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


def test_process_article_extracts_and_scores() -> None:
    processed = process_article(_raw())
    assert "CVE-2024-11111" in processed.entities.cves
    assert "insider threat" in processed.entities.keywords_hit
    assert "data exfiltration" in processed.entities.keywords_hit
    hit_ids = {h.id for h in processed.entities.itm_hits}
    assert "IF001" in hit_ids or "ME005" in hit_ids
    assert "MT003" in hit_ids or any(i.startswith("MT003") for i in hit_ids)
    assert "<p>" not in (processed.summary or "")
    assert processed.relevance_score > 0.2
    assert "Insider threat" in processed.clean_text
    assert processed.embedding is not None
    assert len(processed.embedding) == 256
    assert processed.itm_alignment == "insider"


def test_run_processing_writes_processed_store(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save([_raw(), _raw(link="https://example.com/other", title="Generic update")])

    result = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert result.articles_read == 2
    assert result.articles_saved == 2
    assert processed_path.exists()

    # Second run de-dupes
    again = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert again.articles_skipped == 2
    assert again.articles_saved == 0


def test_content_feeds_clean_text_but_not_summary() -> None:
    raw = _raw(
        content=(
            "The defendant, a former engineer, staged proprietary schematics "
            "on a personal cloud account before resigning."
        )
    )
    processed = process_article(raw)
    assert "personal cloud account" in processed.clean_text
    assert "personal cloud account" not in (processed.summary or "")


def test_refreshed_raw_article_is_reprocessed(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save([_raw(), _raw(link="https://example.com/other", title="Generic update")])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    updated = _raw(summary="<p>Employee also sabotaged backups before departure.</p>")
    assert store.refresh([updated]) == (0, 1)

    result = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert result.articles_processed == 1  # only the refreshed link
    assert result.articles_skipped == 1

    from apps.aggregator.processed_storage import JsonlProcessedStore

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 2  # upserted, not duplicated
    by_link = {r.link: r for r in rows}
    assert "sabotaged backups" in by_link["https://example.com/insider-alert"].clean_text


def test_naive_processed_at_does_not_crash(tmp_path: Path) -> None:
    import json

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    # Simulate a legacy row with a naive processed_at timestamp.
    row = json.loads(processed_path.read_text().splitlines()[0])
    row["processed_at"] = "2030-01-01T00:00:00"
    processed_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert result.articles_skipped == 1
    assert not result.errors


def test_processed_article_carries_classification() -> None:
    raw = _raw(
        title="Confession: overemployed and hiding it",
        link="https://reddit.com/r/overemployed/post1",
        summary=(
            "I'm working two remote jobs (J1 and J2) without telling either "
            "employer. Our outside employment policy forbids it but everyone does it."
        ),
        source_id="social-reddit-overemployed",
        source_name="Reddit r/overemployed",
        channel="social",
    )
    processed = process_article(raw)
    assert processed.channel == "social"
    assert "overemployment" in processed.use_cases
    assert processed.insider_type == "negligent"
    assert processed.classification_source == "heuristic"
    assert processed.classification_confidence is None


def test_unclassified_article_has_defaults() -> None:
    raw = _raw(
        title="Quarterly roadmap update",
        link="https://example.com/roadmap",
        summary="<p>The all-hands covered the roadmap and a new office.</p>",
    )
    processed = process_article(raw)
    assert processed.use_cases == []
    assert processed.insider_type is None
    assert processed.classification_source is None


def test_retag_reprocess_classifies_off_prior_llm_motive_hits() -> None:
    """A --force re-tag reprocess must classify off the FULL adjudicated hit set.

    LLM-adjudicated Motive hits carried from a prior enrichment used to be
    merged only AFTER the classify node ran, so an enriched insider case whose
    text has no heuristic cue phrases stayed insider_type=None on every re-tag
    sweep — despite the pipeline having already paid for a Motive adjudication
    that the curated MT→disposition mapping covers.
    """
    from datetime import UTC, datetime

    from shared.schemas.articles import ExtractedEntities, ItmHit
    from shared.schemas.forensics import PerCaseForensics

    raw = _raw(
        title="Former engineer moved customer files before departure",
        link="https://example.com/untyped-case",
        summary="An engineer transferred a large volume of files shortly before resigning.",
    )
    first = process_article(raw)
    assert first.insider_type is None  # no cue phrases, nothing to corroborate

    # Prior enrichment adjudicated MT005 (Personal Gain — a malicious Motive).
    prior = first.model_copy(
        update={
            "entities": first.entities.model_copy(
                update={
                    "itm_hits": [
                        *first.entities.itm_hits,
                        ItmHit(
                            id="MT005",
                            title="Personal Gain",
                            theme="motive",
                            article_id="AR1",
                            source="llm",
                        ),
                    ]
                }
            ),
            "forensics": PerCaseForensics(
                link="https://example.com/untyped-case",
                title="Former engineer moved customer files before departure",
                is_insider_case=True,
                extracted_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
        }
    )
    assert isinstance(prior.entities, ExtractedEntities)

    reprocessed = process_article(raw, prior=prior)
    assert reprocessed.insider_type == "malicious"
    assert reprocessed.classification_source == "heuristic"
    # The carried adjudication also survives in the stored entity view.
    assert any(h.id == "MT005" and h.source == "llm" for h in reprocessed.entities.itm_hits)


def test_old_shape_processed_row_still_loads(tmp_path: Path) -> None:
    """Rows written before the classification fields existed must parse."""
    import json

    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    JsonlArticleStore(raw_path).save([_raw()])
    run_processing(raw_path=raw_path, processed_path=processed_path)

    row = json.loads(processed_path.read_text().splitlines()[0])
    for key in (
        "use_cases",
        "insider_type",
        "classification_source",
        "classification_confidence",
    ):
        row.pop(key, None)
    processed_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    from apps.aggregator.processed_storage import JsonlProcessedStore

    rows = JsonlProcessedStore(processed_path).load_all()
    assert len(rows) == 1
    assert rows[0].use_cases == []
    assert rows[0].insider_type is None


def test_filing_articles_cluster_by_docket() -> None:
    docket = _raw(
        title="United States v. Example",
        link="https://www.courtlistener.com/docket/1/example/",
        summary=(
            "Court: District Court, S.D. New York\n"
            "Docket: 1:24-cr-00001\n"
            "Cause: 18:1832 Trade Secrets\n"
            "CourtListener query: q"
        ),
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
        published="2024-06-01T00:00:00Z",
    )
    opinion = _raw(
        title="US v. Example (2d Cir. opinion)",
        link="https://www.courtlistener.com/opinion/9/example/",
        summary=(
            "Court: District Court, S.D. New York\n"
            "Docket: 1:24-cr-00001\n"
            "the employee copied files\n"
            "CourtListener query: q"
        ),
        source_id="courtlistener-opinions",
        source_name="CourtListener Opinions",
        channel="filings",
        published="2025-02-10T00:00:00Z",
    )
    key_a = process_article(docket).story_key
    key_b = process_article(opinion).story_key
    assert key_a == key_b  # same case, different day/type/title

    no_docket = _raw(
        title="Misc filing",
        link="https://www.courtlistener.com/docket/2/misc/",
        summary="Court: D. Mass\nCourtListener query: q",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )
    assert process_article(no_docket).story_key not in {key_a, ""}
