"""Jurisdiction (country) facet: resolver, index filter, ledger slice, export.

Jurisdiction = the court system of the source records, never the actor's
nationality. One ledger engine renders the global view and every per-country
view; the absent param must behave exactly like today's global behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime

from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle
from shared.schemas.articles import ExtractedEntities, LegalMetadata
from shared.utils.evidence import (
    build_evidence_ledger,
    filter_rows_by_country,
    resolve_country,
)


def _article(
    *, source_id: str, channel: str = "filings", legal: LegalMetadata | None = None
) -> ProcessedArticle:
    return ProcessedArticle(
        title=f"case via {source_id}",
        link=f"https://example.com/{source_id}",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        source_id=source_id,
        source_name=source_id,
        channel=channel,  # type: ignore[arg-type]
        summary="Court: X\nDocket: 1",
        clean_text="clean",
        entities=ExtractedEntities(),
        relevance_score=0.9,
        itm_alignment="insider",
        legal_metadata=legal,
    )


def test_resolve_country_metadata_wins_over_prefix() -> None:
    assert resolve_country("indiacourts-judgments") == "IN"
    assert resolve_country("courtlistener-recap") == "US"
    assert resolve_country("pacer-purchase") == "US"
    assert resolve_country("canlii-onsc") == "CA"
    assert resolve_country("krebsonsecurity") is None  # news has no jurisdiction
    # Explicit legal metadata beats the prefix fallback.
    assert resolve_country("courtlistener-recap", {"country_code": "gb"}) == "GB"
    assert resolve_country("", {"country_code": ""}) is None


def test_index_country_filter_and_hit_fields() -> None:
    legal = LegalMetadata(country_code="IN", cnr="HCBM01", court_name="Bombay High Court")
    index = ArticleSearchIndex(
        [
            _article(source_id="indiacourts-judgments", legal=legal),
            _article(source_id="courtlistener-recap"),
            _article(source_id="krebs", channel="news"),
        ]
    )
    everything = index.list_articles(limit=10, min_score=0, itm_alignment="all")
    assert everything.count == 3  # absent param = today's behavior
    only_in = index.list_articles(limit=10, min_score=0, itm_alignment="all", country="IN")
    assert only_in.count == 1
    hit = only_in.results[0]
    assert hit.country == "IN"
    assert hit.legal_metadata is not None and hit.legal_metadata.cnr == "HCBM01"
    only_us = index.list_articles(limit=10, min_score=0, itm_alignment="all", country="us")
    assert only_us.count == 1 and only_us.results[0].country == "US"
    # Sources listing honors the same facet.
    src_in = index.list_sources(min_score=0, itm_alignment="all", country="IN")
    assert [s[0] for s in src_in] == ["indiacourts-judgments"]


def _ledger_row(source_id: str, *, verdict: bool = True, legal: dict | None = None) -> dict:
    return {
        "link": f"https://example.com/{source_id}",
        "title": source_id,
        "published": "2026-01-01T00:00:00+00:00",
        "source_id": source_id,
        "legal_metadata": legal,
        "forensics": {
            "is_insider_case": verdict,
            "legal_posture": "conviction",
            "methods": [
                {
                    "action": "copied files",
                    "claim_status": "adjudicated",
                    "observables": [],
                }
            ],
        },
    }


def test_ledger_countries_breakdown_and_slicing() -> None:
    rows = [
        _ledger_row("indiacourts-judgments", legal={"country_code": "IN"}),
        _ledger_row("courtlistener-recap"),
        _ledger_row("courtlistener-opinions"),
        _ledger_row("krebs"),  # no jurisdiction — counted globally, no country
    ]
    ledger = build_evidence_ledger(rows, now=datetime(2026, 8, 22, tzinfo=UTC))
    assert ledger["enriched_cases"] == 4
    assert ledger["countries"] == {"US": 2, "IN": 1}

    sliced = build_evidence_ledger(
        filter_rows_by_country(rows, "IN"), now=datetime(2026, 8, 22, tzinfo=UTC)
    )
    assert sliced["enriched_cases"] == 1
    assert sliced["countries"] == {"IN": 1}
    # Slicing one jurisdiction never changes another's numbers.
    us = build_evidence_ledger(
        filter_rows_by_country(rows, "US"), now=datetime(2026, 8, 22, tzinfo=UTC)
    )
    assert us["enriched_cases"] == 2


def test_ledger_global_equals_unfiltered_rows() -> None:
    """The absent param IS the global computation — same rows, same payload."""
    rows = [
        _ledger_row("indiacourts-judgments", legal={"country_code": "IN"}),
        _ledger_row("courtlistener-recap"),
    ]
    now = datetime(2026, 8, 22, tzinfo=UTC)
    assert build_evidence_ledger(rows, now=now) == build_evidence_ledger(list(rows), now=now)


def test_export_rows_carry_country_and_legal_metadata() -> None:
    from apps.aggregator.export import article_to_export_row, filter_articles

    legal = LegalMetadata(country_code="IN", cnr="HCBM01", source_terms="CC BY 4.0")
    india = _article(source_id="indiacourts-judgments", legal=legal)
    us = _article(source_id="courtlistener-recap")
    row = article_to_export_row(india)
    assert row["country"] == "IN"
    assert row["legal_metadata"]["cnr"] == "HCBM01"
    assert article_to_export_row(us)["legal_metadata"] is None
    assert article_to_export_row(us)["country"] == "US"

    kept = filter_articles([india, us], itm_alignment="all", country="IN")
    assert [a.source_id for a in kept] == ["indiacourts-judgments"]
    assert len(filter_articles([india, us], itm_alignment="all")) == 2


def test_legacy_rows_without_legal_metadata_still_parse() -> None:
    """Pre-feature JSONL rows must load untouched (pydantic default None)."""
    payload = _article(source_id="courtlistener-recap").model_dump(mode="json")
    payload.pop("legal_metadata", None)
    row = ProcessedArticle.model_validate(payload)
    assert row.legal_metadata is None
