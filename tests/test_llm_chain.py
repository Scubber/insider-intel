"""Tests for the multi-provider fallback chain + custom OpenAI-compatible endpoints."""

from __future__ import annotations

import json

from shared.agents.summarize import SummaryBudget, enrich_fields
from shared.llm import (
    get_summarizer_chain,
    get_summarizer_provider,
    reset_provider_cache,
)
from shared.schemas.articles import ItmHit
from shared.settings import Settings


def _settings(**overrides) -> Settings:
    reset_provider_cache()
    base = {"CORS_ORIGINS": "http://127.0.0.1:5500"}
    base.update(overrides)
    return Settings(**base)


# --- chain construction --------------------------------------------------------


def test_empty_provider_is_off() -> None:
    assert get_summarizer_chain(_settings()) == []
    assert get_summarizer_provider(_settings()) is None


def test_chain_skips_providers_without_keys() -> None:
    # anthropic + gemini named but only OpenAI funded → both keyless entries drop.
    chain = get_summarizer_chain(
        _settings(SUMMARIZER_LLM_PROVIDER="anthropic,openai,gemini", OPENAI_API_KEY="sk-x")
    )
    assert [p.model_name for p in chain] == ["gpt-4o-mini"]


def test_model_override_applies_to_primary_only() -> None:
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="openai,gemini",
            OPENAI_API_KEY="sk-x",
            GEMINI_API_KEY="g-x",
            SUMMARIZER_MODEL="gpt-4o",
        )
    )
    # Primary uses the override; the fallback keeps its provider default.
    assert [p.model_name for p in chain] == ["gpt-4o", "gemini-2.5-flash"]


def test_custom_openai_compatible_provider(monkeypatch) -> None:
    monkeypatch.setenv("SOL_KEY", "sol-secret")
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="sol,openai",
            OPENAI_API_KEY="sk-x",
            LLM_CUSTOM_PROVIDERS=json.dumps(
                {
                    "sol": {
                        "base_url": "https://sol.example/v1",
                        "model": "sol-5.6",
                        "api_key_env": "SOL_KEY",
                    }
                }
            ),
        )
    )
    assert [p.model_name for p in chain] == ["sol-5.6", "gpt-4o-mini"]


def test_xai_grok_builtin_provider() -> None:
    chain = get_summarizer_chain(
        _settings(SUMMARIZER_LLM_PROVIDER="xai,openai", XAI_API_KEY="xai-x", OPENAI_API_KEY="sk-x")
    )
    assert [p.model_name for p in chain] == ["grok-4", "gpt-4o-mini"]
    # The "grok" alias resolves to the same provider; no key → skipped.
    assert get_summarizer_chain(_settings(SUMMARIZER_LLM_PROVIDER="grok")) == []


def test_sol_reuses_openai_key_and_endpoint(monkeypatch) -> None:
    # Mirrors the prod config: SOL is a custom OpenAI-compatible provider on the
    # OpenAI endpoint, reusing OPENAI_API_KEY — no new secret.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="openai,sol",
            OPENAI_API_KEY="sk-real",
            LLM_CUSTOM_PROVIDERS=json.dumps(
                {
                    "sol": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-5.6-sol",
                        "api_key_env": "OPENAI_API_KEY",
                    }
                }
            ),
        )
    )
    assert [p.model_name for p in chain] == ["gpt-4o-mini", "gpt-5.6-sol"]
    sol = chain[1]
    assert sol._base_url == "https://api.openai.com/v1"
    assert sol._api_key == "sk-real"  # pulled from api_key_env


def test_timeout_knob_reaches_openai_compat_providers() -> None:
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-x",
            OPENAI_COMPAT_TIMEOUT_SECONDS="300",
        )
    )
    assert [p._timeout for p in chain] == [300.0]
    # Default stays the historical 90s.
    chain = get_summarizer_chain(_settings(SUMMARIZER_LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"))
    assert [p._timeout for p in chain] == [90.0]


def test_single_provider_string_still_works() -> None:
    chain = get_summarizer_chain(_settings(SUMMARIZER_LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"))
    assert len(chain) == 1
    assert (
        get_summarizer_provider(_settings(SUMMARIZER_LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"))
        is not None
    )


def test_malformed_custom_map_degrades_to_empty() -> None:
    chain = get_summarizer_chain(
        _settings(SUMMARIZER_LLM_PROVIDER="sol", LLM_CUSTOM_PROVIDERS="{not json")
    )
    assert chain == []  # unknown 'sol' with no valid custom map → dropped


# --- fallback behaviour in enrich_fields --------------------------------------


class _Fake:
    def __init__(self, model_name, reply):
        self.model_name = model_name
        self.reply = reply
        self.calls = 0

    def extract_case(self, **kwargs):
        self.calls += 1
        return self.reply


class _Exploding:
    model_name = "boom"

    def __init__(self):
        self.calls = 0

    def extract_case(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider down")


_GOOD_REPLY = {
    "ai_summary": "note",
    "is_insider_case": True,
    "methods": [{"action": "copied files"}],
}


def _enrich(chain, monkeypatch, budget=None):
    monkeypatch.setattr("shared.agents.summarize.get_summarizer_chain", lambda settings: chain)
    return enrich_fields(
        title="US v. Example insider",
        source="courtlistener-recap",
        text=("x " * 800) + "copied files to a USB drive for data exfiltration",
        lexical_hits=[
            ItmHit(
                id="IF002", title="t", theme="Exfiltration", article_id="AF001", source="lexical"
            )
        ],
        use_cases=[],
        settings=_settings(),
        budget=budget or SummaryBudget(5),
    )


def test_fallback_uses_next_provider_on_failure(monkeypatch) -> None:
    primary = _Exploding()
    secondary = _Fake("backup-model", _GOOD_REPLY)
    summary, forensics, record, _ = _enrich([primary, secondary], monkeypatch)
    assert primary.calls == 1 and secondary.calls == 1
    assert forensics is not None and forensics.model == "backup-model"
    assert summary == "note"


def test_fallback_on_none_reply(monkeypatch) -> None:
    primary = _Fake("empty", None)  # returns None → try next
    secondary = _Fake("backup", _GOOD_REPLY)
    _, forensics, _, _ = _enrich([primary, secondary], monkeypatch)
    assert primary.calls == 1 and secondary.calls == 1
    assert forensics is not None and forensics.model == "backup"


def test_all_providers_failing_returns_floor(monkeypatch) -> None:
    a, b = _Exploding(), _Exploding()
    summary, forensics, record, hits = _enrich([a, b], monkeypatch)
    assert a.calls == 1 and b.calls == 1
    assert (summary, forensics, record, hits) == (None, None, None, [])


def test_budget_consumed_once_regardless_of_fallbacks(monkeypatch) -> None:
    budget = SummaryBudget(5)
    _enrich([_Exploding(), _Fake("ok", _GOOD_REPLY)], monkeypatch, budget=budget)
    assert budget.spent == 1  # one article = one budget unit, not one-per-attempt


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


def test_custom_auto_model_probes_v1_models(monkeypatch) -> None:
    def fake_get(url, headers=None, timeout=None):
        assert url == "http://vllm:8000/v1/models"
        return _HttpResp({"data": [{"id": "Qwen/Qwen3.8-27B-FP8"}]})

    monkeypatch.setattr("shared.llm.openai_provider.httpx.get", fake_get)
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="sparky",
            LLM_CUSTOM_PROVIDERS=json.dumps(
                {
                    "sparky": {
                        "base_url": "http://vllm:8000/v1",
                        "model": "auto",
                        "api_key_env": "SPARKY_API_KEY",
                    }
                }
            ),
        )
    )
    assert [p.model_name for p in chain] == ["Qwen/Qwen3.8-27B-FP8"]


def test_auto_probe_sends_bearer_header(monkeypatch) -> None:
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers or {}
        return _HttpResp({"data": [{"id": "served-model"}]})

    monkeypatch.setattr("shared.llm.openai_provider.httpx.get", fake_get)
    monkeypatch.setenv("SPARKY_API_KEY", "probe-secret")
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="sparky",
            LLM_CUSTOM_PROVIDERS=json.dumps(
                {
                    "sparky": {
                        "base_url": "http://vllm:8000/v1",
                        "model": "auto",
                        "api_key_env": "SPARKY_API_KEY",
                    }
                }
            ),
        )
    )
    assert [p.model_name for p in chain] == ["served-model"]
    assert seen["headers"].get("Authorization") == "Bearer probe-secret"


_SPARKY_AUTO = json.dumps({"sparky": {"base_url": "http://vllm:8000/v1", "model": "auto"}})


def test_auto_probe_retries_transient_failure(monkeypatch) -> None:
    import httpx as httpx_mod

    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx_mod.ConnectError("connection refused")
        return _HttpResp({"data": [{"id": "served-after-retry"}]})

    monkeypatch.setattr("shared.llm.openai_provider.httpx.get", flaky_get)
    monkeypatch.setattr("shared.llm.openai_provider.time.sleep", lambda s: None)
    chain = get_summarizer_chain(
        _settings(SUMMARIZER_LLM_PROVIDER="sparky", LLM_CUSTOM_PROVIDERS=_SPARKY_AUTO)
    )
    assert calls["n"] == 2
    assert [p.model_name for p in chain] == ["served-after-retry"]


def test_auto_probe_failure_is_loud_and_not_cached(monkeypatch, caplog) -> None:
    # A dead probe must drop the provider for THIS resolution only: the empty
    # chain is never cached (a vLLM restart mid-run heals on the next article),
    # and the failure logs [FAIL] lines — an empty chain returns before
    # budget.take(), so the enrichment tripwire can't see this state.
    import logging

    import httpx as httpx_mod

    def dead_get(url, headers=None, timeout=None):
        raise httpx_mod.ConnectError("connection refused")

    monkeypatch.setattr("shared.llm.openai_provider.httpx.get", dead_get)
    monkeypatch.setattr("shared.llm.openai_provider.time.sleep", lambda s: None)
    settings = _settings(SUMMARIZER_LLM_PROVIDER="sparky", LLM_CUSTOM_PROVIDERS=_SPARKY_AUTO)
    with caplog.at_level(logging.ERROR):
        assert get_summarizer_chain(settings) == []
    assert "[FAIL] llm-probe" in caplog.text
    assert "[FAIL] llm-chain" in caplog.text

    # Same settings object, NO cache reset: once the server answers, the very
    # next resolution succeeds.
    monkeypatch.setattr(
        "shared.llm.openai_provider.httpx.get",
        lambda url, headers=None, timeout=None: _HttpResp({"data": [{"id": "recovered"}]}),
    )
    assert [p.model_name for p in get_summarizer_chain(settings)] == ["recovered"]


def test_auto_probe_rejects_malformed_bodies(monkeypatch) -> None:
    # Shape problems are terminal (no retry can fix them) but never raise.
    for body in ({"data": "nope"}, {"data": []}, {"data": [{"id": ""}]}, ["not", "a", "dict"]):
        monkeypatch.setattr(
            "shared.llm.openai_provider.httpx.get",
            lambda url, headers=None, timeout=None, body=body: _HttpResp(body),
        )
        monkeypatch.setattr("shared.llm.openai_provider.time.sleep", lambda s: None)
        chain = get_summarizer_chain(
            _settings(SUMMARIZER_LLM_PROVIDER="sparky", LLM_CUSTOM_PROVIDERS=_SPARKY_AUTO)
        )
        assert chain == [], f"body {body!r} should drop the provider"


def test_enrichment_stamps_served_model_from_completion(monkeypatch) -> None:
    from shared.llm.openai_provider import OpenAICompatSummarizer

    def fake_post(url, json=None, headers=None, timeout=None):
        import json as json_mod

        payload = {
            "model": "Qwen/Qwen3.8-27B-FP8",
            "choices": [{"message": {"content": json_mod.dumps(_GOOD_REPLY)}}],
        }
        return _HttpResp(payload)

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    provider = OpenAICompatSummarizer(base_url="http://vllm:8000/v1", model="stale-pin")
    summary, forensics, _, _ = _enrich([provider], monkeypatch)
    assert provider.model_name == "Qwen/Qwen3.8-27B-FP8"
    assert forensics is not None and forensics.model == "Qwen/Qwen3.8-27B-FP8"
    assert summary == "note"


# --- enable_thinking knob -------------------------------------------------------


def test_thinking_off_adds_chat_template_kwargs(monkeypatch) -> None:
    from shared.llm.openai_provider import OpenAICompatSummarizer

    seen: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["payload"] = json
        import json as json_mod

        return _HttpResp(
            {"model": "m", "choices": [{"message": {"content": json_mod.dumps(_GOOD_REPLY)}}]}
        )

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    provider = OpenAICompatSummarizer(base_url="http://v:8000/v1", model="m", enable_thinking=False)
    provider.extract_case(title="t", source="courtlistener-recap", text="x", itm_candidates="")
    assert seen["payload"]["chat_template_kwargs"] == {"enable_thinking": False}

    # Default (thinking on) sends NOTHING — non-vLLM payloads stay byte-identical.
    provider = OpenAICompatSummarizer(base_url="http://v:8000/v1", model="m")
    provider.extract_case(title="t", source="courtlistener-recap", text="x", itm_candidates="")
    assert "chat_template_kwargs" not in seen["payload"]


def test_thinking_knob_threads_from_settings() -> None:
    chain = get_summarizer_chain(
        _settings(
            SUMMARIZER_LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-x",
            OPENAI_COMPAT_ENABLE_THINKING="false",
        )
    )
    assert [p._enable_thinking for p in chain] == [False]
    chain = get_summarizer_chain(_settings(SUMMARIZER_LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"))
    assert [p._enable_thinking for p in chain] == [True]


def test_400_naming_chat_template_kwargs_strips_and_retries(monkeypatch) -> None:
    from shared.llm.openai_provider import OpenAICompatSummarizer

    calls: list = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(dict(json))
        if len(calls) == 1:
            return _HttpResp({}, status=400, text="chat_template_kwargs is not supported")
        import json as json_mod

        return _HttpResp(
            {"model": "m", "choices": [{"message": {"content": json_mod.dumps(_GOOD_REPLY)}}]}
        )

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    provider = OpenAICompatSummarizer(base_url="http://v:8000/v1", model="m", enable_thinking=False)
    result = provider.extract_case(
        title="t", source="courtlistener-recap", text="x", itm_candidates=""
    )
    assert result is not None
    assert "chat_template_kwargs" in calls[0]
    assert "chat_template_kwargs" not in calls[1]
