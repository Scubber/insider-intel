"""Anthropic classifier (claude-haiku-4-5 by default — cheap/fast labels)."""

from __future__ import annotations

import logging

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    ClassificationResult,
    build_user_prompt,
)
from shared.llm.openai_provider import _parse_result

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
