"""OpenAI-compatible chat-completions providers (Ollama, vLLM, LM Studio, xAI...)."""

from __future__ import annotations

import json
import logging
import re

import httpx

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    DISCOVER_SYSTEM_PROMPT,
    ENRICH_SYSTEM_PROMPT,
    ClassificationResult,
    build_discover_prompt,
    build_enrich_prompt,
    build_user_prompt,
)

# Unified enrichment produces a large JSON (analyst note + full forensic
# record + case_record + ITM adjudication). Rich court filings (many methods,
# long detected-via/outcome, multi-paragraph note) exceeded the old 4000-token
# cap, clipping the JSON and cutting whatever serialized last (case-record
# fields). 12000 gives the fullest filings room; every provider in the chain
# (Haiku/Sonnet/Opus 4.5+) supports far more.
ENRICH_MAX_TOKENS = 12000
# Discovery output is just per-method assessments — far smaller than enrich.
DISCOVER_MAX_TOKENS = 2000

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _chat_completion(
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
    system: str,
    user: str,
    max_tokens: int | None = None,
) -> str | None:
    """POST a JSON-mode chat completion; returns the reply text or None."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    url = f"{base_url}/chat/completions"
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        if response.status_code == 400 and "response_format" in response.text:
            # Some local servers reject response_format; retry without it.
            payload.pop("response_format", None)
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("OpenAI-compat chat call failed: %s", exc)
        return None


class OpenAICompatClassifier:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def classify(self, *, title: str, text: str) -> ClassificationResult | None:
        content = _chat_completion(
            base_url=self._base_url,
            model=self._model,
            api_key=self._api_key,
            timeout=self._timeout,
            system=CLASSIFY_SYSTEM_PROMPT,
            user=build_user_prompt(title, text),
        )
        if content is None:
            return None
        return _parse_result(content)


class OpenAICompatSummarizer:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_input_chars: int = 6000,
        timeout: float = 90.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_input_chars = max_input_chars
        self._timeout = timeout
        self.model_name = model

    def extract_case(
        self, *, title: str, source: str, text: str, itm_candidates: str
    ) -> dict | None:
        content = _chat_completion(
            base_url=self._base_url,
            model=self._model,
            api_key=self._api_key,
            timeout=self._timeout,
            system=ENRICH_SYSTEM_PROMPT,
            user=build_enrich_prompt(
                title=title,
                source=source,
                text=text,
                itm_candidates=itm_candidates,
                max_chars=self._max_input_chars,
            ),
            max_tokens=ENRICH_MAX_TOKENS,
        )
        if content is None:
            return None
        return _parse_json_object(content, label="Enricher")


class OpenAICompatDiscoverer:
    """Second-pass novel-technique discovery over the forensic record."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_input_chars: int = 12000,
        timeout: float = 90.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_input_chars = max_input_chars
        self._timeout = timeout
        self.model_name = model

    def discover_techniques(self, *, forensics_json: str, itm_shortlist: str) -> dict | None:
        content = _chat_completion(
            base_url=self._base_url,
            model=self._model,
            api_key=self._api_key,
            timeout=self._timeout,
            system=DISCOVER_SYSTEM_PROMPT,
            user=build_discover_prompt(
                forensics_json=forensics_json,
                itm_shortlist=itm_shortlist,
                max_chars=self._max_input_chars,
            ),
            max_tokens=DISCOVER_MAX_TOKENS,
        )
        if content is None:
            return None
        return _parse_json_object(content, label="Discoverer")


def _parse_json_object(content: str, *, label: str) -> dict | None:
    raw = (content or "").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        match = _JSON_RE.search(raw)
        if not match:
            logger.warning("%s reply had no JSON object", label)
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            logger.warning("%s reply JSON did not parse", label)
            return None
    if not isinstance(data, dict):
        logger.warning("%s reply JSON was not an object", label)
        return None
    return data


def _parse_result(content: str) -> ClassificationResult | None:
    data = _parse_json_object(content, label="Classifier")
    if data is None:
        return None
    try:
        return ClassificationResult.model_validate(data).sanitized()
    except ValueError as exc:
        logger.warning("Classifier reply failed validation: %s", exc)
        return None
