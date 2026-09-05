"""The stdlib corpus readers in shared/utils/evidence.py agree with the store.

``JsonlProcessedStore.upsert`` APPENDS (never rewrites) so one link can
occupy several lines mid-cycle; ``load_all`` reads last-line-wins. Every
stdlib script that reads the raw JSONL must apply the same dedupe in the
same direction — the 2026-08 email scan deduped first-wins and reported the
stale generation. Also pins the industry slice against the pydantic enum.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.schemas import ProcessedArticle
from shared.schemas.forensics import INDUSTRIES, CaseMethod, PerCaseForensics
from shared.utils.evidence import (
    INDUSTRY_LABELS,
    build_evidence_ledger,
    collapse_rows_by_link,
    filter_rows_by_industry,
    iter_jsonl_rows,
    resolve_actor_employer_sector,
    resolve_industry,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _article(link: str, *, industry: str = "unknown", confidence: float = 0.5) -> ProcessedArticle:
    forensics = PerCaseForensics(
        link=link,
        title=link,
        candidate_technique_ids=["IF002"],
        methods=[CaseMethod(action="copied files", claim_status="adjudicated")],
        is_insider_case=True,
        confidence=confidence,
        industry=industry,
    )
    return ProcessedArticle(
        title=link,
        link=link,
        published=NOW,
        source_id="example",
        source_name="Example",
        clean_text="an employee copied files",
        forensics=forensics,
    )


def _dump(article: ProcessedArticle) -> str:
    return json.dumps(json.loads(article.model_dump_json()), sort_keys=True)


def test_collapse_rows_matches_store_load_all(tmp_path) -> None:
    path = tmp_path / "articles.jsonl"
    store = JsonlProcessedStore(path)
    a = _article("https://ex.com/a", confidence=0.3)
    b = _article("https://ex.com/b")
    store.save([a, b])
    a2 = _article("https://ex.com/a", industry="financial-services", confidence=0.9)
    store.upsert([a2])
    # upsert appends: the FILE holds a's link twice.
    assert sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line) == 3

    got = [json.dumps(r, sort_keys=True) for r in collapse_rows_by_link(iter_jsonl_rows(path))]
    want = [_dump(x) for x in store.load_all()]
    assert got == want
    assert [json.loads(g)["link"] for g in got] == [a.link, b.link]  # first-seen order
    assert json.loads(got[0])["forensics"]["industry"] == "financial-services"  # last wins


def test_iter_jsonl_rows_skips_blank_and_corrupt_lines(tmp_path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"link": "a"}\n\n{torn\n[1, 2]\n{"link": "b"}', encoding="utf-8")
    assert [r["link"] for r in iter_jsonl_rows(path)] == ["a", "b"]


def test_collapse_keeps_linkless_rows() -> None:
    rows = [{"v": 1}, {"link": "a", "v": 1}, {"v": 2}, {"link": "a", "v": 3}]
    assert collapse_rows_by_link(rows) == [{"v": 1}, {"link": "a", "v": 3}, {"v": 2}]


def test_industry_labels_mirror_pydantic_enum() -> None:
    """The stdlib core cannot import the schema; this is the drift tripwire."""
    assert set(INDUSTRY_LABELS) == set(INDUSTRIES)


def test_resolve_actor_employer_sector_reads_the_additive_field_only() -> None:
    """The insider's OWN employer's sector; the victim's stays in ``industry``."""
    assert resolve_actor_employer_sector({"actor_employer_sector": "Healthcare"}) == "healthcare"
    assert resolve_actor_employer_sector({"actor_employer_sector": None}) == "unknown"
    assert resolve_actor_employer_sector({"actor_employer_sector": "crypto"}) == "unknown"
    # Never falls back to the victim's sector.
    assert resolve_actor_employer_sector({"industry": "healthcare"}) == "unknown"
    assert resolve_actor_employer_sector({}) == "unknown"
    assert resolve_actor_employer_sector(None) == "unknown"
    assert resolve_actor_employer_sector({"actor_employer_sector": ["x"]}) == "unknown"
    for label in INDUSTRIES:
        assert resolve_actor_employer_sector({"actor_employer_sector": label}) == label


def test_resolve_industry_falls_back_to_unknown() -> None:
    assert resolve_industry({"industry": "Financial-Services"}) == "financial-services"
    assert resolve_industry({"industry": "crypto"}) == "unknown"
    assert resolve_industry({}) == "unknown"
    assert resolve_industry(None) == "unknown"


def _ledger_row(n: int, industry: str | None) -> dict:
    f = {
        "is_insider_case": True,
        "candidate_technique_ids": ["IF002"],
        "methods": [{"action": "copied files", "claim_status": "adjudicated"}],
    }
    if industry is not None:
        f["industry"] = industry
    return {
        "link": f"https://ex.com/{n}",
        "title": str(n),
        "published": "2026-01-01",
        "forensics": f,
    }


def test_ledger_slices_by_industry() -> None:
    rows = [
        _ledger_row(1, "financial-services"),
        _ledger_row(2, "financial-services"),
        _ledger_row(3, "healthcare"),
        _ledger_row(4, None),  # pre-v3: resolves to unknown
    ]
    assert build_evidence_ledger(rows, now=NOW)["enriched_cases"] == 4
    fs = build_evidence_ledger(filter_rows_by_industry(rows, "financial-services"), now=NOW)
    assert fs["enriched_cases"] == 2
    hc = build_evidence_ledger(filter_rows_by_industry(rows, "HEALTHCARE"), now=NOW)
    assert hc["enriched_cases"] == 1
    assert len(filter_rows_by_industry(rows, "unknown")) == 1


def test_cli_slices_before_building(tmp_path) -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "evidence_ledger.py"
    spec = importlib.util.spec_from_file_location("evidence_ledger_cli_slice", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    rows = [
        _ledger_row(1, "financial-services") | {"source_id": "courtlistener-recap"},
        _ledger_row(2, "healthcare") | {"source_id": "courtlistener-recap"},
        _ledger_row(3, "financial-services") | {"source_id": "indiacourts-judgments"},
    ]
    assert len(cli.slice_rows(rows)) == 3
    assert len(cli.slice_rows(rows, country="all", industry="*")) == 3
    assert len(cli.slice_rows(rows, industry="financial-services")) == 2
    assert len(cli.slice_rows(rows, country="us")) == 2
    assert len(cli.slice_rows(rows, country="US", industry="financial-services")) == 1

    corpus = tmp_path / "c.jsonl"
    corpus.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "l.json"
    assert cli.main([str(corpus), "--industry", "financial-services", "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["enriched_cases"] == 2
