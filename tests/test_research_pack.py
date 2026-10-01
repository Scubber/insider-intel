"""Contracts for scripts/research_pack.py — the briefing author's corpus pull.

Load-bearing: the verdict gate (only verdict-true, method-bearing rows count
as cases), posture never summed, the redaction choke point (a seeded email
local part never prints), the small-n floor (no percentages under 10), and
the BEGIN/END contract the research routine parses out of the job log.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "research_pack.py"
_spec = importlib.util.spec_from_file_location("research_pack", _SCRIPT)
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

LOCAL_PART = "zq7tunique"


def _row(
    link,
    *,
    title="Acme Corp v. Doe",
    text="",
    verdict=True,
    methods=None,
    posture="complaint",
    published="2024-03-01T00:00:00Z",
    source_id="courtlistener",
    schema=3,
    industry="technology",
    role="software engineer",
    detection="",
    tools=None,
    summary="",
    techniques=None,
    exfil=None,
):
    f = {
        "is_insider_case": verdict,
        "methods": methods
        if methods is not None
        else [
            {
                "action": "copied the source repository to a personal drive",
                "claim_status": "alleged",
            }
        ],
        "legal_posture": posture,
        "schema_version": schema,
        "industry": industry,
        "actor_role": role,
        "actor_profile": f"a {role} who resigned",
        "detection": detection,
        "tool_mentions": tools or [],
        "candidate_technique_ids": techniques or ["IF002"],
        "exfil_channels": exfil or ["cloud storage"],
        "confidence": 0.8,
    }
    return {
        "link": link,
        "title": title,
        "clean_text": text,
        "published": published,
        "source_id": source_id,
        "channel": "filings",
        "ai_summary": summary,
        "forensics": f,
    }


def _spec_(**kw):
    base = {
        "query": ".",
        "techniques": [],
        "country": "all",
        "industry": "all",
        "posture": "any",
        "year_from": None,
        "scope": "full",
        "min_hits": 1,
    }
    base.update(kw)
    return base


def test_verdict_gate_and_funnel() -> None:
    rows = [
        _row("a", text="trade secret"),
        _row("b", text="trade secret", verdict=False),
        _row("c", text="trade secret", verdict=None),
        _row("d", text="trade secret", methods=[]),
        _row("e", text="patent only"),
    ]
    pack = rp.build_pack(rows, spec=_spec_(query="trade secret"), cases_n=10)
    fu = pack["funnel"]
    assert fu["rows"] == 5 and fu["matched_any"] == 4 and fu["matched_cases"] == 1
    assert pack["matched_other"] == {
        "enricher adjudicated NOT an insider case": 1,
        "no verdict stored": 1,
        "no extracted methods (un-enriched or floor)": 1,
    }


def test_posture_is_split_never_summed_and_capped_by_legal_posture() -> None:
    rows = [
        _row("a", methods=[{"action": "x", "claim_status": "adjudicated"}], posture="conviction"),
        # A complaint caps an "adjudicated" method at alleged (POSTURE_WEIGHT cap).
        _row("b", methods=[{"action": "x", "claim_status": "adjudicated"}], posture="complaint"),
        _row("c", methods=[{"action": "x", "claim_status": "reported"}], posture="unknown"),
    ]
    pack = rp.build_pack(rows, spec=_spec_(), cases_n=10)
    assert pack["aggregate"]["strength"] == {
        "adjudicated/admitted": 1,
        "alleged": 1,
        "reported/unclear": 1,
    }
    md = rp.render_markdown(pack)
    assert "Posture (never summed)" in md
    only_adj = rp.build_pack(rows, spec=_spec_(posture="adjudicated"), cases_n=10)
    assert only_adj["funnel"]["matched_cases"] == 1


def test_slices_country_industry_technique_year() -> None:
    rows = [
        _row("us", source_id="courtlistener", published="2024-01-01"),
        _row("in", source_id="indiacourts-delhi", published="2019-01-01", techniques=["IF016"]),
        _row("old", source_id="courtlistener", published="2012-01-01", industry="healthcare"),
    ]
    assert rp.build_pack(rows, spec=_spec_(country="IN"), cases_n=5)["funnel"]["matched_cases"] == 1
    assert (
        rp.build_pack(rows, spec=_spec_(industry="healthcare"), cases_n=5)["funnel"][
            "matched_cases"
        ]
        == 1
    )
    assert (
        rp.build_pack(rows, spec=_spec_(techniques=["IF016"]), cases_n=5)["funnel"]["matched_cases"]
        == 1
    )
    assert (
        rp.build_pack(rows, spec=_spec_(year_from=2015), cases_n=5)["funnel"]["matched_cases"] == 2
    )
    # scope=meta ignores the document body.
    rows2 = [_row("m", text="only in the body: thumbdrive", summary="")]
    assert (
        rp.build_pack(rows2, spec=_spec_(query="thumbdrive"), cases_n=5)["funnel"]["matched_cases"]
        == 1
    )
    assert (
        rp.build_pack(rows2, spec=_spec_(query="thumbdrive", scope="meta"), cases_n=5)["funnel"][
            "matched_cases"
        ]
        == 0
    )


def test_redaction_choke_point_and_cards() -> None:
    rows = [
        _row(
            "a",
            summary=f"Sent files to {LOCAL_PART}@gmail.com before resigning.",
            detection=f"A colleague noticed mail to {LOCAL_PART}@proton.me",
            methods=[
                {
                    "action": f"forwarded to {LOCAL_PART}@yahoo.com",
                    "claim_status": "alleged",
                    "quantity": "4,000 files",
                }
            ],
        )
    ]
    pack = rp.build_pack(rows, spec=_spec_(), cases_n=5)
    md = rp.render_markdown(pack)
    blob = md + json.dumps(pack)
    assert LOCAL_PART not in blob
    assert "<redacted>@gmail.com" in md and "<redacted>@proton.me" in md
    card = pack["cards"][0]
    assert card["actor"].startswith("engineering") or "/" in card["actor"]
    assert card["methods"] and "4,000 files" in md
    assert "Case cards" in md and "link: a" in md


def test_detection_families_and_tools() -> None:
    assert (
        rp.detection_family("A forensic image of the laptop after her departure showed")
        == "forensic exam after departure"
    )
    assert rp.detection_family("A DLP alert flagged the upload") == "monitoring or DLP alert"
    assert rp.detection_family("A co-worker reported the download") == "colleague or manager report"
    assert rp.detection_family("") == "not stated"
    assert rp.detection_family("something else entirely") == "other / unclassified"
    rows = [
        _row("a", detection="audit of access logs", tools=[{"name": "Varonis", "role": "caught"}]),
        _row(
            "b", detection="audit of access logs", tools=[{"name": "DLP Suite", "role": "bypassed"}]
        ),
    ]
    agg = rp.build_pack(rows, spec=_spec_(), cases_n=5)["aggregate"]
    assert agg["detection"][0] == ("audit or log review", 2)
    assert agg["tools"] == {"bypassed": [("DLP Suite", 1)], "caught": [("Varonis", 1)]}


def test_small_n_floor_suppresses_percentages() -> None:
    few = [_row(f"r{i}") for i in range(4)]
    md = rp.render_markdown(rp.build_pack(few, spec=_spec_(), cases_n=5))
    assert "SMALL N" in md and "%" not in md.split("## Posture")[1].split("## By year")[0]
    many = [_row(f"r{i}") for i in range(12)]
    md2 = rp.render_markdown(rp.build_pack(many, spec=_spec_(), cases_n=5))
    assert "(100%)" in md2 and "SMALL N" not in md2


def test_industry_only_counted_on_v3_rows() -> None:
    rows = [_row("v3", schema=3, industry="defense"), _row("v2", schema=2, industry="defense")]
    agg = rp.build_pack(rows, spec=_spec_(), cases_n=5)["aggregate"]
    assert agg["industry"] == [("defense", 1)] and agg["industry_not_asked"] == 1


def test_cli_and_workflow_markers(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(
        "\n".join(json.dumps(_row(f"r{i}")) for i in range(3)) + "\n", encoding="utf-8"
    )
    out = tmp_path / "pack.json"
    rc = rp.main([str(corpus), "--query", ".", "--cases", "2", "--json", str(out)])
    assert rc == 0
    md = capsys.readouterr().out
    assert md.startswith("# insider-intel research pack")
    assert json.loads(out.read_text())["funnel"]["matched_cases"] == 3
    wf = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "research-pack.yml"
    ).read_text()
    assert "=== RESEARCH PACK BEGIN ===" in wf and "=== RESEARCH PACK END ===" in wf
    assert "scripts/research_pack.py" in wf
    playbook = (
        Path(__file__).resolve().parent.parent / "docs" / "research" / "routine-prompt.md"
    ).read_text()
    assert "=== RESEARCH PACK BEGIN ===" in playbook and "research-pack.yml" in playbook
