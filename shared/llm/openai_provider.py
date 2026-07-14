"""OpenAI-compatible chat-completions classifier (Ollama, vLLM, LM Studio, xAI...)."""

from __future__ import annotations

import json
import logging
import re

import httpx

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    ClassificationResult,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


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
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(title, text)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        url = f"{self._base_url}/chat/completions"
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
            if response.status_code == 400 and "response_format" in response.text:
                # Some local servers reject response_format; retry without it.
                payload.pop("response_format", None)
                response = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            logger.warning("OpenAI-compat classify call failed: %s", exc)
            return None
        return _parse_result(content)


def _parse_result(content: str) -> ClassificationResult | None:
    raw = (content or "").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        match = _JSON_RE.search(raw)
        if not match:
            logger.warning("Classifier reply had no JSON object")
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            logger.warning("Classifier reply JSON did not parse")
            return None
    try:
        return ClassificationResult.model_validate(data).sanitized()
    except ValueError as exc:
        logger.warning("Classifier reply failed validation: %s", exc)
        return None
