"""Boot snapshot exporter: ArticleListResponse-shaped, slim, field-complete."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import CaseMethod, PerCaseForensics
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
                    context_kind="" if insider else "detection",
                    legal_posture="indictment" if insider else "unknown",
                    methods=[
                        CaseMethod(
                            action="Copied customer data to USB",
                            tools=["robocopy"],
                            claim_status="adjudicated" if insider else "reported",
                            evidence_quote="dropped by the slimmer",
                        )
                    ],
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

    # Slimmed: no case_record; forensics reduced to what the stream card
    # renders (stamps, proof label, posture badge, METHODS fact line).
    raw = json.loads(json.dumps(articles))
    for row in raw["results"]:
        assert "case_record" not in row or row["case_record"] is None
        if row.get("forensics") is not None:
            from scripts.export_boot_snapshot import _KEEP_FORENSICS_KEYS

            assert set(row["forensics"].keys()) == set(_KEEP_FORENSICS_KEYS) | {"methods"}
            # Heavy detail must not leak into the first-paint payload.
            for dropped in ("timeline", "hunt_terms", "hunt_queries", "observables"):
                assert dropped not in row["forensics"]
            for method in row["forensics"]["methods"]:
                assert set(method.keys()) == {"action", "claim_status"}

    # Fields the stream card needs survive.
    flags = {row["forensics"]["is_insider_case"] for row in raw["results"] if row.get("forensics")}
    assert flags == {True, False}
    assert all(row.get("ai_summary") for row in raw["results"])
    # Purpose stamp + proof label inputs survive the slimming.
    by_flag = {
        row["forensics"]["is_insider_case"]: row["forensics"]
        for row in raw["results"]
        if row.get("forensics")
    }
    assert by_flag[False]["context_kind"] == "detection"
    assert by_flag[True]["legal_posture"] == "indictment"
    assert by_flag[True]["methods"][0]["claim_status"] == "adjudicated"

    assert meta["indexed_articles"] == 2
    assert meta["generated_at"]
    # Verdict-gated ledger basis rides in meta.json for the cached-first-paint
    # EVIDENCE staleness banner: 2 enriched rows, 1 insider-True contributor.
    basis = meta["evidence_basis"]
    assert basis["generated_at"]
    assert basis["corpus_rows"] == 2 and basis["enriched_rows"] == 2
    assert basis["verdict_true_rows"] == 1 and basis["contributing_cases"] == 1
    assert basis["excluded_non_insider"] == 1 and basis["excluded_no_verdict"] == 0
    assert "quote_verbatim_share_pct" in basis


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


def test_snapshot_covers_stream_card_forensics_reads() -> None:
    """Contract: every forensics field web/app.js reads on the stream card must
    survive the exporter's slimming — a new read without a matching whitelist
    entry ships bare CACHED rows (this exact bug shipped once: context_kind and
    claim_status were stripped, so stamps and proof labels only appeared LIVE).
    """
    import re
    from pathlib import Path

    from scripts.export_boot_snapshot import _KEEP_FORENSICS_KEYS, _KEEP_METHOD_KEYS

    app_js = Path("web/app.js").read_text(encoding="utf-8")

    # Direct reads: article.forensics.<field> anywhere in the client.
    fields = set(re.findall(r"\bforensics\.([a-z_]+)", app_js))

    # Aliased reads inside caseFacts() — `const f = (article && article.forensics)`.
    case_facts = re.search(r"function caseFacts\(article\)\s*\{.*?\n  \}", app_js, re.DOTALL)
    assert case_facts, "caseFacts() not found in web/app.js — update this contract test"
    fields |= set(re.findall(r"\bf\.([a-z_]+)", case_facts.group(0)))

    kept = set(_KEEP_FORENSICS_KEYS) | {"methods"}
    missing = fields - kept
    assert not missing, (
        f"web/app.js reads forensics fields the boot snapshot drops: {sorted(missing)} — "
        "add them to _KEEP_FORENSICS_KEYS in scripts/export_boot_snapshot.py"
    )

    # The proof label + METHODS fact line read these off each method entry.
    proof = re.search(r"function proofLabel\(article\)\s*\{.*?\n  \}", app_js, re.DOTALL)
    assert proof, "proofLabel() not found in web/app.js — update this contract test"
    method_fields = set(re.findall(r"\b[am]\.([a-z_]+)", proof.group(0)))
    method_fields |= {"action"}  # caseFacts maps m.action for the fact strip
    assert method_fields <= set(_KEEP_METHOD_KEYS), (
        f"method fields read but dropped: {sorted(method_fields - set(_KEEP_METHOD_KEYS))}"
    )
