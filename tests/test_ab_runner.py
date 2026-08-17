"""A/B runner: per-arm knob plumbing, pair records, checkpoint/resume, safety."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ab_select_goldset import iter_processed
from scripts.ab_thinking_run import (
    ARM_OFF,
    ARM_ON,
    build_arm_providers,
    build_arm_settings,
    first_arm_for,
    load_done_links,
    run_pairs,
)
from shared.llm import reset_provider_cache
from shared.settings import Settings
from tests.ab_helpers import FakeSummarizer, make_row

_TEXT = "The engineer copied client files to a personal Google Drive before resigning. " * 80
_VERBATIM_QUOTE = "copied client files to a personal Google Drive"
_PARAPHRASE_QUOTE = "moved corporate documents into private cloud storage"


def _settings(**overrides) -> Settings:
    reset_provider_cache()
    base = {"CORS_ORIGINS": "http://127.0.0.1:5500"}
    base.update(overrides)
    return Settings(**base)


def _reply(quote: str, *, insider: bool = True) -> dict:
    return {
        "ai_summary": "analyst note",
        "is_insider_case": insider,
        "confidence": 0.8,
        "legal_posture": "indictment",
        "methods": [{"action": "copied files", "evidence_quote": quote}],
        "hunt_terms": ["google drive"],
    }


def _corpus(n: int = 3) -> dict:
    rows = [make_row(f"https://x.test/run-{i}", clean_text=_TEXT) for i in range(n)]
    return {row.link: row for row in rows}


def _manifest(corpus: dict) -> dict:
    return {"cases": [{"link": link} for link in sorted(corpus)]}


# --- knob plumbing through the existing chain machinery -------------------------


def test_arm_settings_differ_only_in_thinking_knob() -> None:
    base = _settings(SUMMARIZER_LLM_PROVIDER="openai")
    on, off = build_arm_settings(base, base_url="http://vllm:8000/v1", model="q3", timeout=900)
    assert on.openai_compat_enable_thinking is True
    assert off.openai_compat_enable_thinking is False
    for arm in (on, off):
        assert arm.summarizer_provider_chain() == ["openai"]
        assert arm.openai_compat_base_url == "http://vllm:8000/v1"
        assert arm.summarizer_model == "q3"
        assert arm.openai_compat_timeout_seconds == 900.0


def test_arm_providers_carry_the_knob_and_share_everything_else() -> None:
    base = _settings(SUMMARIZER_LLM_PROVIDER="openai")
    on_settings, off_settings = build_arm_settings(base, base_url="http://vllm:8000/v1", model="q3")
    provider_on, provider_off = build_arm_providers(on_settings, off_settings)
    assert provider_on is not provider_off  # the chain cache must not fuse the arms
    assert provider_on._enable_thinking is True
    assert provider_off._enable_thinking is False
    assert provider_on._base_url == provider_off._base_url == "http://vllm:8000/v1"
    assert provider_on.model_name == provider_off.model_name == "q3"


# --- pair records ---------------------------------------------------------------


def test_run_pairs_records_both_arms_with_stamps_and_usage(tmp_path: Path) -> None:
    corpus = _corpus(2)
    pairs_path = tmp_path / "ab_pairs.jsonl"
    provider_on = FakeSummarizer(
        default_reply=_reply(_VERBATIM_QUOTE),
        model_name="qwen-on",
        last_usage={"completion_tokens": 4200, "prompt_tokens": 900, "total_tokens": 5100},
    )
    provider_off = FakeSummarizer(default_reply=_reply(_PARAPHRASE_QUOTE), model_name="qwen-off")
    summary = run_pairs(
        manifest=_manifest(corpus),
        corpus_by_link=corpus,
        provider_on=provider_on,
        provider_off=provider_off,
        settings=Settings(),
        pairs_path=pairs_path,
        log=lambda *a: None,
    )
    assert summary["ran"] == 2 and summary["missing_rows"] == 0
    lines = [json.loads(line) for line in pairs_path.read_text().splitlines()]
    assert len(lines) == 2
    for pair in lines:
        on, off = pair[ARM_ON], pair[ARM_OFF]
        assert on["parse_ok"] and off["parse_ok"]
        assert pair["first_arm"] == first_arm_for(pair["link"])
        assert on["model"] == "qwen-on" and off["model"] == "qwen-off"
        assert on["wall_seconds"] >= 0.0
        assert on["completion_tokens"] == 4200 and off["completion_tokens"] is None
        # Grounding stamp replayed at write time: verbatim copy vs paraphrase.
        assert on["forensics"]["methods"][0]["evidence_quote_verbatim"] is True
        assert off["forensics"]["methods"][0]["evidence_quote_verbatim"] is False
        assert on["input_chars"] <= len(_TEXT)


def test_run_pairs_missing_row_skipped_without_write(tmp_path: Path) -> None:
    corpus = _corpus(1)
    manifest = {"cases": [{"link": "https://x.test/ghost"}, *_manifest(corpus)["cases"]]}
    pairs_path = tmp_path / "ab_pairs.jsonl"
    summary = run_pairs(
        manifest=manifest,
        corpus_by_link=corpus,
        provider_on=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        provider_off=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        settings=Settings(),
        pairs_path=pairs_path,
        log=lambda *a: None,
    )
    assert summary["missing_rows"] == 1 and summary["ran"] == 1
    assert len(pairs_path.read_text().splitlines()) == 1


# --- checkpoint / resume --------------------------------------------------------


def test_run_pairs_resumes_and_skips_done(tmp_path: Path) -> None:
    corpus = _corpus(3)
    manifest = _manifest(corpus)
    pairs_path = tmp_path / "ab_pairs.jsonl"
    kwargs = dict(
        manifest=manifest,
        corpus_by_link=corpus,
        settings=Settings(),
        pairs_path=pairs_path,
        log=lambda *a: None,
    )

    first = run_pairs(
        provider_on=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        provider_off=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        limit=2,
        **kwargs,
    )
    assert first["ran"] == 2

    resume_on = FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE))
    resume_off = FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE))
    second = run_pairs(provider_on=resume_on, provider_off=resume_off, **kwargs)
    assert second["ran"] == 1 and second["skipped_done"] == 2
    assert len(resume_on.calls) == 1 and len(resume_off.calls) == 1  # only the remaining case
    links = [json.loads(line)["link"] for line in pairs_path.read_text().splitlines()]
    assert sorted(links) == sorted(corpus) and len(set(links)) == 3


def test_load_done_links_ignores_torn_and_partial_lines(tmp_path: Path) -> None:
    pairs_path = tmp_path / "ab_pairs.jsonl"
    good = {"link": "https://x.test/a", ARM_ON: {"parse_ok": True}, ARM_OFF: {"parse_ok": False}}
    partial = {"link": "https://x.test/b", ARM_ON: {"parse_ok": True}}
    pairs_path.write_text(
        json.dumps(good) + "\n" + json.dumps(partial) + "\n" + '{"torn": ',
        encoding="utf-8",
    )
    assert load_done_links(pairs_path) == {"https://x.test/a"}
    assert load_done_links(tmp_path / "missing.jsonl") == set()


def test_provider_exception_is_a_recorded_data_point(tmp_path: Path) -> None:
    corpus = _corpus(2)
    titles = sorted(row.title for row in corpus.values())
    pairs_path = tmp_path / "ab_pairs.jsonl"
    summary = run_pairs(
        manifest=_manifest(corpus),
        corpus_by_link=corpus,
        provider_on=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        provider_off=FakeSummarizer(
            default_reply=_reply(_VERBATIM_QUOTE), raise_titles=(titles[0],)
        ),
        settings=Settings(),
        pairs_path=pairs_path,
        log=lambda *a: None,
    )
    assert summary["ran"] == 2  # the failure did not stop the run
    lines = [json.loads(line) for line in pairs_path.read_text().splitlines()]
    failed = next(p for p in lines if not p[ARM_OFF]["parse_ok"])
    assert "RuntimeError" in failed[ARM_OFF]["error"]
    assert failed[ARM_OFF]["forensics"] is None
    assert failed[ARM_ON]["parse_ok"] is True


# --- corpus safety --------------------------------------------------------------


def test_corpus_file_is_never_written(tmp_path: Path) -> None:
    corpus_path = tmp_path / "articles.jsonl"
    rows = [make_row(f"https://x.test/safe-{i}", clean_text=_TEXT) for i in range(2)]
    corpus_path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")
    before = corpus_path.read_bytes()
    corpus = {row.link: row for row in iter_processed(corpus_path)}
    run_pairs(
        manifest=_manifest(corpus),
        corpus_by_link=corpus,
        provider_on=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        provider_off=FakeSummarizer(default_reply=_reply(_VERBATIM_QUOTE)),
        settings=Settings(),
        pairs_path=tmp_path / "out" / "ab_pairs.jsonl",
        log=lambda *a: None,
    )
    assert corpus_path.read_bytes() == before


# --- usage side-channel on the real provider ------------------------------------


class _HttpResp:
    def __init__(self, payload, status=200, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")

    def json(self):
        return self._payload


def test_get_last_usage_captures_and_resets(monkeypatch) -> None:
    from shared.llm.openai_provider import OpenAICompatSummarizer, get_last_usage

    bodies = [
        {
            "model": "m",
            "choices": [{"message": {"content": json.dumps(_reply(""))}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        },
        {"model": "m", "choices": [{"message": {"content": json.dumps(_reply(""))}}]},
    ]

    def fake_post(url, json=None, headers=None, timeout=None):
        return _HttpResp(bodies.pop(0))

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    provider = OpenAICompatSummarizer(base_url="http://v:8000/v1", model="m")
    provider.extract_case(title="t", source="courtlistener-recap", text="x", itm_candidates="")
    assert get_last_usage() == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    # A reply without usage resets the side-channel — no stale carry-over.
    provider.extract_case(title="t", source="courtlistener-recap", text="x", itm_candidates="")
    assert get_last_usage() is None
