"""Anthropic classifier (claude-haiku-4-5 by default — cheap/fast labels)."""

from __future__ import annotations

import logging

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    SUMMARIZE_SYSTEM_PROMPT,
    CaseExtractionResult,
    ClassificationResult,
    build_summarize_prompt,
    build_user_prompt,
)
from shared.llm.openai_provider import _parse_case_result, _parse_result

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
    ) -> CaseExtractionResult | None:
        prompt = build_summarize_prompt(
            title=title,
            source=source,
            text=text,
            itm_candidates=itm_candidates,
            max_chars=self._max_input_chars,
        )
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=800,
                system=SUMMARIZE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.warning("Anthropic summarize call failed: %s", exc)
            return None
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return _parse_case_result("".join(parts))
