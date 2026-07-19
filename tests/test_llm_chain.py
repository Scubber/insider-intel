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
        text="x" * 2000,
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
