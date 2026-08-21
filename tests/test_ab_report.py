"""Report: mechanical metrics, verdict logic both directions, replay, judge."""

from __future__ import annotations

import json

from scripts.ab_thinking_report import (
    AGREEMENT_TOLERANCE,
    MIN_SPEEDUP,
    VERBATIM_MAX_DROP_POINTS,
    compute_metrics,
    decide,
    judge_order_for,
    load_pairs,
    render_markdown,
    run_judge,
)
from scripts.ab_thinking_run import ARM_OFF, ARM_ON
from shared.llm.openai_provider import ChatResult
from tests.ab_helpers import make_row


def _arm(
    *,
    parse_ok: bool = True,
    insider: bool = True,
    wall: float = 10.0,
    conf: float = 0.8,
    quotes: tuple = (("copied client files", True),),
    hunt_terms: tuple = ("usb",),
    tokens: int | None = 1_000,
    input_chars: int = 10_000,
) -> dict:
    forensics = None
    if parse_ok:
        forensics = {
            "is_insider_case": insider,
            "confidence": conf,
            "detection": "a DLP alert flagged the export" if insider else None,
            "outcome": None,
            "methods": [
                {
                    "action": "did a thing",
                    "claim_status": "alleged",
                    "evidence_quote": quote,
                    "evidence_quote_verbatim": stamp,
                }
                for quote, stamp in quotes
            ],
            "hunt_terms": list(hunt_terms),
            "hunt_queries": [],
        }
    return {
        "parse_ok": parse_ok,
        "error": None if parse_ok else "boom",
        "wall_seconds": wall,
        "completion_tokens": tokens,
        "input_chars": input_chars,
        "forensics": forensics,
    }


def _pair(link: str, on: dict, off: dict) -> dict:
    return {"link": link, "title": f"Case {link}", "first_arm": ARM_ON, ARM_ON: on, ARM_OFF: off}


def _manifest(links: list[str], *, baseline_verdict: bool | None = True) -> dict:
    cases = []
    for link in links:
        case: dict = {"link": link}
        if baseline_verdict is not None:
            case["baseline"] = {"model": "claude-sonnet-5", "is_insider_case": baseline_verdict}
        cases.append(case)
    return {"cases": cases}


def _good_pairs(n: int = 10) -> list[dict]:
    """All-green dataset: off is 2x faster, everything else identical."""
    return [
        _pair(f"https://x.test/p{i}", _arm(wall=100.0), _arm(wall=50.0, tokens=500))
        for i in range(n)
    ]


# --- metrics --------------------------------------------------------------------


def test_metrics_shapes_and_values() -> None:
    links = [f"https://x.test/p{i}" for i in range(4)]
    pairs = [
        _pair(links[0], _arm(wall=100.0), _arm(wall=50.0)),
        _pair(links[1], _arm(wall=80.0), _arm(wall=40.0, insider=False)),  # arms disagree
        _pair(links[2], _arm(wall=120.0), _arm(parse_ok=False, wall=30.0)),  # off parse fail
        _pair(links[3], _arm(wall=100.0), _arm(wall=50.0)),
    ]
    metrics = compute_metrics(pairs, _manifest(links))
    on, off = metrics["arms"][ARM_ON], metrics["arms"][ARM_OFF]
    assert on["parse_failure_rate"] == 0.0 and off["parse_failure_rate"] == 0.25
    assert metrics["both_parsed"] == 3
    assert metrics["verdict_agreement_between_arms"] == round(2 / 3, 4)
    # Baseline verdict is True everywhere: on agrees 4/4; off agrees 2/3 parsed.
    assert metrics["baseline_agreement"][ARM_ON] == 1.0
    assert metrics["baseline_agreement"][ARM_OFF] == round(2 / 3, 4)
    assert metrics["baseline_pairs"] == {ARM_ON: 4, ARM_OFF: 3}
    # Speedup over both-parsed pairs: mean(100, 80, 100) / mean(50, 40, 50).
    assert metrics["speedup_on_over_off"] == 2.0
    assert on["verbatim_rate"] == 1.0
    assert on["hunt_terms_present_rate"] == 1.0
    assert on["completion_tokens"]["n"] == 4


def test_verbatim_replay_overrides_runner_stamps() -> None:
    text = "The engineer copied client files to a personal drive. " * 50
    link = "https://x.test/replay"
    # Runner stamps stored WRONG both ways: the verbatim quote stamped False,
    # the paraphrase stamped True — the replay must recompute both from text.
    pairs = [
        _pair(
            link,
            _arm(quotes=(("copied client files", False),), input_chars=len(text)),
            _arm(quotes=(("stole many documents", True),), input_chars=len(text)),
        )
    ]
    corpus = {link: make_row(link, clean_text=text)}
    metrics = compute_metrics(pairs, _manifest([link]), corpus)
    assert metrics["verbatim_source"] == "replayed against stored clean_text"
    assert metrics["arms"][ARM_ON]["verbatim_rate"] == 1.0
    assert metrics["arms"][ARM_OFF]["verbatim_rate"] == 0.0

    # Without a corpus the stored stamps are used, and the report says so.
    metrics = compute_metrics(
        [
            _pair(
                link,
                _arm(quotes=(("copied client files", False),)),
                _arm(quotes=(("stole many documents", True),)),
            )
        ],
        _manifest([link]),
    )
    assert metrics["verbatim_source"].startswith("runner stamps")
    assert metrics["arms"][ARM_ON]["verbatim_rate"] == 0.0


# --- verdict logic, both directions ---------------------------------------------


def test_decide_activates_when_all_criteria_hold() -> None:
    decision = decide(compute_metrics(_good_pairs(), _manifest([p["link"] for p in _good_pairs()])))
    assert decision["recommendation"] == "ACTIVATE"
    assert decision["failed_criteria"] == []
    assert {c["name"] for c in decision["checks"]} == {
        "parse_failure",
        "baseline_agreement",
        "verbatim_floor",
        "detection_fill",
        "verbatim_rate",
        "speedup",
    }
    assert decision["constants"] == {
        "AGREEMENT_TOLERANCE": AGREEMENT_TOLERANCE,
        "VERBATIM_MAX_DROP_POINTS": VERBATIM_MAX_DROP_POINTS,
        "MIN_SPEEDUP": MIN_SPEEDUP,
    }


def test_decide_keeps_thinking_on_slow_speedup() -> None:
    pairs = [_pair(f"https://x.test/p{i}", _arm(wall=100.0), _arm(wall=80.0)) for i in range(10)]
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    assert decision["recommendation"] == "KEEP_THINKING"
    assert decision["failed_criteria"] == ["speedup"]


def test_decide_keeps_thinking_on_parse_regression() -> None:
    pairs = _good_pairs(9) + [
        _pair("https://x.test/fail", _arm(wall=100.0), _arm(parse_ok=False, wall=50.0))
    ]
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    assert "parse_failure" in decision["failed_criteria"]
    assert decision["recommendation"] == "KEEP_THINKING"


def test_decide_keeps_thinking_on_verbatim_drop() -> None:
    # on: 10/10 verbatim; off: 8/10 → a 20-point drop > the 5-point allowance.
    pairs = []
    for i in range(10):
        off_stamp = i >= 2
        pairs.append(
            _pair(
                f"https://x.test/p{i}",
                _arm(wall=100.0, quotes=(("q", True),)),
                _arm(wall=50.0, quotes=(("q", off_stamp),)),
            )
        )
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    assert decision["failed_criteria"] == ["verbatim_rate"]


def test_decide_keeps_thinking_on_baseline_agreement_drop() -> None:
    # Baseline True everywhere; off flips 2 of 10 verdicts → 0.8 < 0.95 * 1.0.
    pairs = []
    for i in range(10):
        pairs.append(
            _pair(
                f"https://x.test/p{i}",
                _arm(wall=100.0),
                _arm(wall=50.0, insider=i >= 2),
            )
        )
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    assert "baseline_agreement" in decision["failed_criteria"]


def test_decide_vacuous_passes_are_named() -> None:
    pairs = [_pair("https://x.test/p0", _arm(wall=100.0, quotes=()), _arm(wall=50.0, quotes=()))]
    decision = decide(
        compute_metrics(pairs, _manifest(["https://x.test/p0"], baseline_verdict=None))
    )
    by_name = {c["name"]: c for c in decision["checks"]}
    assert by_name["baseline_agreement"]["passed"] is True
    assert "vacuously" in by_name["baseline_agreement"]["detail"]
    assert by_name["verbatim_rate"]["passed"] is True
    assert "vacuously" in by_name["verbatim_rate"]["detail"]
    assert decision["recommendation"] == "ACTIVATE"  # remaining criteria all hold


# --- judge (separate, never in the verdict) -------------------------------------


def test_judge_is_blind_and_maps_winners_back(tmp_path) -> None:
    text = "source text " * 100
    links = [f"https://x.test/j{i}" for i in range(6)]
    pairs = [_pair(link, _arm(), _arm()) for link in links]
    corpus = {link: make_row(link, clean_text=text) for link in links}
    seen_prompts: list[str] = []

    def fake_chat(**kwargs):
        seen_prompts.append(kwargs["user"])
        return ChatResult(content=json.dumps({"better": 1, "reason": "better grounded"}))

    judge = run_judge(
        pairs,
        corpus,
        base_url="http://v/v1",
        model="m",
        api_key=None,
        timeout=60,
        seed=7,
        chat=fake_chat,
    )
    assert judge["judged"] == 6 and judge["errors"] == 0
    # "Record 1 always wins" must split by the blinded per-link ordering.
    expect_on = sum(1 for link in links if judge_order_for(link, 7)[0] == ARM_ON)
    assert judge["thinking_on_wins"] == expect_on
    assert judge["thinking_off_wins"] == 6 - expect_on
    for prompt in seen_prompts:
        assert "thinking" not in prompt.lower()  # arm names never reach the judge
        assert "RECORD 1:" in prompt and "RECORD 2:" in prompt
    assert "model-grades-model" in judge["caveat"]


def test_judge_counts_ties_and_unparseable_replies() -> None:
    link = "https://x.test/j"
    pairs = [_pair(link, _arm(), _arm())]
    corpus = {link: make_row(link)}
    judge = run_judge(
        pairs,
        corpus,
        base_url="b",
        model="m",
        api_key=None,
        timeout=60,
        seed=1,
        chat=lambda **kw: ChatResult(content='{"better": 0, "reason": "same"}'),
    )
    assert judge["ties"] == 1 and judge["judged"] == 1
    judge = run_judge(
        pairs,
        corpus,
        base_url="b",
        model="m",
        api_key=None,
        timeout=60,
        seed=1,
        chat=lambda **kw: ChatResult(content="no json here"),
    )
    assert judge["errors"] == 1 and judge["judged"] == 0


# --- IO + rendering -------------------------------------------------------------


def test_load_pairs_dedupes_last_wins_and_skips_corrupt(tmp_path) -> None:
    path = tmp_path / "ab_pairs.jsonl"
    first = _pair("https://x.test/a", _arm(wall=1.0), _arm(wall=1.0))
    second = _pair("https://x.test/a", _arm(wall=9.0), _arm(wall=9.0))
    path.write_text(json.dumps(first) + "\n{torn\n" + json.dumps(second) + "\n", encoding="utf-8")
    pairs = load_pairs(path)
    assert len(pairs) == 1 and pairs[0][ARM_ON]["wall_seconds"] == 9.0


def test_render_markdown_smoke() -> None:
    pairs = _good_pairs()
    metrics = compute_metrics(pairs, _manifest([p["link"] for p in pairs]))
    decision = decide(metrics)
    markdown = render_markdown(metrics, decision, None, {"pairs": "p", "manifest": "m"})
    assert "**ACTIVATE**" in markdown
    for name in (
        "parse_failure",
        "baseline_agreement",
        "verbatim_floor",
        "detection_fill",
        "verbatim_rate",
        "speedup",
    ):
        assert name in markdown
    assert "## LLM judge" not in markdown  # judge section only renders when run

    judge = {
        "caveat": "model-grades-model: circular",
        "judged": 1,
        "thinking_on_wins": 1,
        "thinking_off_wins": 0,
        "ties": 0,
        "errors": 0,
        "seed": 7,
        "per_case": [],
    }
    markdown = render_markdown(metrics, decision, judge, {"pairs": "p", "manifest": "m"})
    assert "LLM judge (optional, informational only)" in markdown
    assert "model-grades-model" in markdown


def test_v3_verbatim_floor_gate() -> None:
    """v3 freeze: the thinking-on arm must clear an ABSOLUTE 85% verbatim rate."""
    mixed = _arm(quotes=(("real quote", True), ("made up", False)))
    pairs = [_pair(f"https://x.test/v{i}", mixed, _arm()) for i in range(4)]
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    by_name = {c["name"]: c for c in decision["checks"]}
    assert by_name["verbatim_floor"]["passed"] is False  # 50% < 85%


def test_v3_detection_fill_gate() -> None:
    """v3 freeze: insider-true records that skip `detection` fail the round."""
    pairs = [_pair(f"https://x.test/d{i}", _arm(), _arm()) for i in range(4)]
    for pr in pairs:
        pr[ARM_ON]["forensics"]["detection"] = None
    decision = decide(compute_metrics(pairs, _manifest([p["link"] for p in pairs])))
    by_name = {c["name"]: c for c in decision["checks"]}
    assert by_name["detection_fill"]["passed"] is False
