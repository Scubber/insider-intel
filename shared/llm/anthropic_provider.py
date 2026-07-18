"""Anthropic classifier (claude-haiku-4-5 by default — cheap/fast labels)."""

from __future__ import annotations

import logging

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    ENRICH_SYSTEM_PROMPT,
    ClassificationResult,
    build_enrich_prompt,
    build_user_prompt,
)
from shared.llm.openai_provider import ENRICH_MAX_TOKENS, _parse_json_object, _parse_result

logger = logging.getLogger(__name__)


class AnthropicClassifier:
    def __init__(self, *, api_key: str, model: str, timeout: float = 60.0) -> None:
        # Imported lazily so the anthropic package stays an optional dep.
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model

    def classify(self, *, title: str, text: str) -> ClassificationResult | None:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                system=CLASSIFY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(title, text)}],
            )
        except Exception as exc:
            logger.warning("Anthropic classify call failed: %s", exc)
            return None
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return _parse_result("".join(parts))


class AnthropicSummarizer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_input_chars: int = 6000,
        timeout: float = 90.0,
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_input_chars = max_input_chars
        self.model_name = model

    def extract_case(
        self, *, title: str, source: str, text: str, itm_candidates: str
    ) -> dict | None:
        prompt = build_enrich_prompt(
            title=title,
            source=source,
            text=text,
            itm_candidates=itm_candidates,
            max_chars=self._max_input_chars,
        )
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=ENRICH_MAX_TOKENS,
                system=ENRICH_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.warning("Anthropic enrich call failed: %s", exc)
            return None
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return _parse_json_object("".join(parts), label="Enricher")
