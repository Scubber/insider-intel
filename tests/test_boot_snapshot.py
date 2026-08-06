"""Boot snapshot exporter: ArticleListResponse-shaped, slim, field-complete."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import PerCaseForensics
from shared.schemas.search import ArticleListResponse


def _seed(tmp_path, monkeypatch):
    rows = []
    for i, insider in enumerate((True, False)):
        art = process_article(
            RawArticle(
                title=f"Insider case {i}: USB exfiltration by former employee",
                link=f"https://example.com/case-{i}",
                summary="Former employee data exfiltration via removable media.",
                published=datetime(2026, 8, 1 + i, tzinfo=UTC),
                source_id="example",
                source_name="Example",
            )
        )
        art = art.model_copy(
            update={
                "ai_summary": f"Analyst note {i}",
                "forensics": PerCaseForensics(
                    link=f"https://example.com/case-{i}",
                    title=f"Insider case {i}",
                    is_insider_case=insider,
                    extracted_at=datetime(2026, 8, 1, tzinfo=UTC),
                ),
            }
        )
        rows.append(art)
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(rows)
    monkeypatch.setenv("PROCESSED_ARTICLES_PATH", str(path))
    return path


def test_snapshot_shape_and_slimming(tmp_path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    from scripts.export_boot_snapshot import build_snapshot

    articles, meta = build_snapshot(limit=50)

    # UI-compatible: validates as the API's stream response model.
    parsed = ArticleListResponse.model_validate(articles)
    assert parsed.total_indexed == 2
    assert parsed.count == len(parsed.results) == 2

    # Slimmed: no case_record, forensics reduced to the CONTEXT-stamp flag.
    raw = json.loads(json.dumps(articles))
    for row in raw["results"]:
        assert "case_record" not in row or row["case_record"] is None
        if row.get("forensics") is not None:
            assert set(row["forensics"].keys()) == {"link", "title", "is_insider_case"}

    # Fields the stream card needs survive.
    flags = {row["forensics"]["is_insider_case"] for row in raw["results"] if row.get("forensics")}
    assert flags == {True, False}
    assert all(row.get("ai_summary") for row in raw["results"])

    assert meta["indexed_articles"] == 2
    assert meta["generated_at"]


def test_snapshot_cli_writes_files(tmp_path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    import sys

    from scripts import export_boot_snapshot

    out = tmp_path / "webdata"
    monkeypatch.setattr(sys, "argv", ["export_boot_snapshot", "--out", str(out)])
    export_boot_snapshot.main()
    data = json.loads((out / "articles.json").read_text())
    assert data["results"]
    assert json.loads((out / "meta.json").read_text())["indexed_articles"] == 2
