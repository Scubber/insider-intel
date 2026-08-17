"""Gold-set selector: stratification, eligibility gate, determinism, read-only."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ab_select_goldset import (
    find_baseline,
    iter_processed,
    length_bucket,
    method_bucket,
    order_key,
    select_goldset,
)
from tests.ab_helpers import make_row


def _varied_corpus() -> list:
    """Rows spanning every stratification axis, some with a strong baseline."""
    rows = []
    for i in range(24):
        rows.append(
            make_row(
                f"https://x.test/case-{i}",
                insider=i % 2 == 0,
                methods_n=(0, 2, 5)[i % 3],
                text_chars=(2_000, 8_000, 25_000)[i % 3],
                posture=("indictment", "none")[i % 2],
                baseline_model="claude-sonnet-5" if i % 4 == 0 else None,
            )
        )
    return rows


def test_buckets() -> None:
    assert [method_bucket(n) for n in (0, 1, 2, 3, 9)] == ["poor", "poor", "mid", "rich", "rich"]
    assert [length_bucket(c) for c in (100, 4_999, 5_000, 19_999, 20_000)] == [
        "short",
        "short",
        "mid",
        "mid",
        "long",
    ]


def test_gate_excludes_short_and_unenriched_rows() -> None:
    rows = [
        make_row("https://x.test/ok", text_chars=6_000),
        make_row("https://x.test/short", text_chars=800),  # below the enrichment gate
        make_row("https://x.test/unenriched", enriched=False),  # nothing to compare against
    ]
    manifest = select_goldset(rows, n=10)
    picked = {c["link"] for c in manifest["cases"]}
    assert picked == {"https://x.test/ok"}
    assert manifest["eligible_rows"] == 1
    assert manifest["corpus_rows"] == 3


def test_stratification_covers_every_axis_and_prefers_baselines() -> None:
    manifest = select_goldset(_varied_corpus(), n=12, seed=7)
    cases = manifest["cases"]
    assert len(cases) == 12
    assert {c["is_insider_case"] for c in cases} == {True, False}
    buckets = {c["method_bucket"] for c in cases}
    assert {"rich", "poor"} <= buckets
    assert {c["length_bucket"] for c in cases} == {"short", "mid", "long"}
    assert len({c["legal_posture"] for c in cases}) >= 2
    with_baseline = [c for c in cases if c["baseline"]]
    assert with_baseline, "baseline-backed rows must be selected first within their cells"
    for case in with_baseline:
        assert case["baseline"]["model"].startswith("claude-sonnet-5")
        assert isinstance(case["baseline"]["history_index"], int)
        assert isinstance(case["baseline"]["is_insider_case"], bool)
    for case in cases:
        assert "cell verdict=" in case["rationale"]
    strata = manifest["strata_counts"]
    assert strata["verdict_true"] + strata["verdict_false"] == 12
    assert strata["with_baseline"] == len(with_baseline)


def test_deterministic_given_seed_and_seed_sensitive_ordering() -> None:
    rows = _varied_corpus()
    a = select_goldset(rows, n=12, seed=7)
    b = select_goldset(rows, n=12, seed=7)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # Ordering is a pure function of (seed, link): different seeds permute it.
    links = [r.link for r in rows]
    assert sorted(links, key=lambda ln: order_key(1, ln)) != sorted(
        links, key=lambda ln: order_key(2, ln)
    )


def test_n_caps_and_exhausts() -> None:
    rows = _varied_corpus()
    assert select_goldset(rows, n=5)["n_selected"] == 5
    manifest = select_goldset(rows, n=500)
    assert manifest["n_selected"] == manifest["eligible_rows"]


def test_find_baseline_picks_newest_matching_generation() -> None:
    row = make_row("https://x.test/b", baseline_model="claude-sonnet-5")
    found = find_baseline(row)
    assert found is not None
    idx, rec = found
    assert rec.model == "claude-sonnet-5"
    assert row.enrichment_history[idx] is rec
    assert find_baseline(make_row("https://x.test/nb")) is None


def test_iter_processed_is_read_only_and_dedupes(tmp_path: Path) -> None:
    corpus = tmp_path / "articles.jsonl"
    old = make_row("https://x.test/dup", insider=False)
    new = make_row("https://x.test/dup", insider=True)
    corpus.write_text(
        old.model_dump_json() + "\n{corrupt\n" + new.model_dump_json() + "\n",
        encoding="utf-8",
    )
    before = corpus.read_bytes()
    rows = iter_processed(corpus)
    assert corpus.read_bytes() == before
    assert len(rows) == 1 and rows[0].forensics.is_insider_case is True  # last line wins
    assert iter_processed(tmp_path / "missing.jsonl") == []


def test_verdict_axis_never_starved_by_cell_count() -> None:
    """Regression: on the real corpus, >= n non-empty verdict-False cells meant
    a sorted-cells round-robin filled the whole gold set before reaching a
    single verdict-True cell (0/40 insider-true picks). Picks must alternate
    the verdict axis, splitting ~evenly whenever both sides have supply."""
    rows = []
    # 45 distinct False cells (unique posture strings force distinct cells).
    for i in range(45):
        rows.append(
            make_row(
                f"https://x.test/false-{i}",
                insider=False,
                methods_n=2,
                text_chars=8_000,
                posture=f"posture-{i}",
            )
        )
    # Ample True supply concentrated in few cells.
    for i in range(30):
        rows.append(
            make_row(
                f"https://x.test/true-{i}",
                insider=True,
                methods_n=5,
                text_chars=25_000,
                posture="conviction",
            )
        )
    manifest = select_goldset(rows, n=40)
    true_n = manifest["strata_counts"]["verdict_true"]
    false_n = manifest["strata_counts"]["verdict_false"]
    assert true_n + false_n == 40
    assert true_n == 20, f"expected an even verdict split, got {true_n}/{false_n}"


def test_cell_order_does_not_starve_a_bucket_value() -> None:
    """Regression: str-sorted cells group by axis value, so a per-verdict
    budget below the cell count starved whichever value sorts last (the real
    corpus drew 0 method-rich picks). Hash-ordered cells must let every
    method bucket through when each has ample supply."""
    rows = []
    for i in range(60):
        rows.append(
            make_row(
                f"https://x.test/r-{i}",
                insider=i % 2 == 0,
                methods_n=(0, 2, 5)[i % 3],
                text_chars=8_000,
                posture=f"posture-{i % 10}",
            )
        )
    manifest = select_goldset(rows, n=30)
    buckets = manifest["strata_counts"]["method_buckets"]
    assert set(buckets) == {"poor", "mid", "rich"}, buckets
