"""OpenAI-compatible chat-completions providers (Ollama, vLLM, LM Studio, xAI...)."""

from __future__ import annotations

import json
import logging
import re

import httpx

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    SUMMARIZE_SYSTEM_PROMPT,
    CaseExtractionResult,
    ClassificationResult,
    build_summarize_prompt,
    build_user_prompt,
)

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
    ) -> CaseExtractionResult | None:
        content = _chat_completion(
            base_url=self._base_url,
            model=self._model,
            api_key=self._api_key,
            timeout=self._timeout,
            system=SUMMARIZE_SYSTEM_PROMPT,
            user=build_summarize_prompt(
                title=title,
                source=source,
                text=text,
                itm_candidates=itm_candidates,
                max_chars=self._max_input_chars,
            ),
        )
        if content is None:
            return None
        return _parse_case_result(content)


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


def _parse_case_result(content: str) -> CaseExtractionResult | None:
    data = _parse_json_object(content, label="Summarizer")
    if data is None:
        return None
    try:
        return CaseExtractionResult.model_validate(data)
    except ValueError as exc:
        logger.warning("Summarizer reply failed validation: %s", exc)
        return None
