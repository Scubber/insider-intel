"""#16 guided JSON: the reply schema and the provider's enforcement ladder."""

from __future__ import annotations

import json

import httpx
import pytest

from shared.llm.base import ENRICH_REPLY_SCHEMA, ENRICH_SYSTEM_PROMPT
from shared.llm.openai_provider import OpenAICompatSummarizer
from shared.schemas.forensics import (
    INDUSTRIES,
    LEGAL_POSTURES,
    SOURCE_TYPES,
    TOOL_MENTION_ROLES,
)


def test_schema_enums_locked_to_storage_constants() -> None:
    """Grammar and storage model must not drift: enums come from one source."""
    props = ENRICH_REPLY_SCHEMA["properties"]
    assert props["industry"]["enum"] == list(INDUSTRIES)
    assert props["source_type"]["enum"] == list(SOURCE_TYPES)
    assert props["legal_posture"]["enum"] == list(LEGAL_POSTURES)
    roles = props["tool_mentions"]["items"]["properties"]["role"]["enum"]
    assert roles == list(TOOL_MENTION_ROLES)


def test_schema_requires_every_contract_key() -> None:
    """Every field the prompt's specimen carries is REQUIRED — unskippable."""
    required = set(ENRICH_REPLY_SCHEMA["required"])
    for key in (
        "ai_summary",
        "is_insider_case",
        "confidence",
        "detection",
        "outcome",
        "exfil_channels",
        "actor_citizenship",
        "industry",
        "tool_mentions",
        "hunt_terms",
    ):
        assert key in required, key
    # v3 removed hunt_queries from the contract entirely.
    assert "hunt_queries" not in required
    assert "hunt_queries" not in ENRICH_REPLY_SCHEMA["properties"]
    assert "hunt_queries" not in ENRICH_SYSTEM_PROMPT


def _summarizer(**kw) -> OpenAICompatSummarizer:
    return OpenAICompatSummarizer(
        base_url="http://vllm.test/v1", model="test-model", api_key="k", **kw
    )


def _reply_body() -> dict:
    return {
        "choices": [{"message": {"content": json.dumps({"ai_summary": "x"})}}],
        "model": "test-model",
    }


def test_extract_case_sends_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json)
        return httpx.Response(200, json=_reply_body(), request=httpx.Request("POST", url))

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    out = _summarizer().extract_case(title="t", source="s", text="body", itm_candidates="")
    assert out == {"ai_summary": "x"}
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] is ENRICH_REPLY_SCHEMA


def test_guided_json_off_falls_back_to_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json)
        return httpx.Response(200, json=_reply_body(), request=httpx.Request("POST", url))

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    _summarizer(guided_json=False).extract_case(
        title="t", source="s", text="body", itm_candidates=""
    )
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_server_rejecting_schema_downgrades_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 on the schema tier retries as plain JSON mode — old servers keep working."""
    seen: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        # Copy: the provider mutates the payload in place between retries.
        seen.append({**json, "response_format": dict(json["response_format"])})
        req = httpx.Request("POST", url)
        if json["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="response_format json_schema unsupported", request=req)
        return httpx.Response(200, json=_reply_body(), request=req)

    monkeypatch.setattr("shared.llm.openai_provider.httpx.post", fake_post)
    out = _summarizer().extract_case(title="t", source="s", text="body", itm_candidates="")
    assert out == {"ai_summary": "x"}
    assert [p["response_format"]["type"] for p in seen] == ["json_schema", "json_object"]
