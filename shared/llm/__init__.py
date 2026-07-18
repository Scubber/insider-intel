"""Optional LLM refiner for use-case / insider-type classification.

Provider is chosen via CLASSIFIER_LLM_PROVIDER (none | anthropic | openai);
"openai" means any OpenAI-compatible endpoint — Ollama/vLLM/LM Studio for
local models, or a hosted service via OPENAI_COMPAT_BASE_URL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shared.llm.base import (
    CaseExtractionResult,
    ClassificationResult,
    ClassifierProvider,
    ItmRef,
    SummarizerProvider,
)

if TYPE_CHECKING:
    from shared.settings import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "CaseExtractionResult",
    "ClassificationResult",
    "ClassifierProvider",
    "ItmRef",
    "SummarizerProvider",
    "get_classifier_provider",
    "get_summarizer_provider",
]

_PROVIDER_CACHE: dict[str, ClassifierProvider | None] = {}
_SUMMARIZER_CACHE: dict[str, SummarizerProvider | None] = {}


def get_classifier_provider(settings: Settings) -> ClassifierProvider | None:
    provider = (settings.classifier_llm_provider or "none").strip().lower()
    if provider in ("", "none"):
        return None
    if provider in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider]

    instance: ClassifierProvider | None = None
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("CLASSIFIER_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY unset")
        else:
            try:
                from shared.llm.anthropic_provider import AnthropicClassifier

                instance = AnthropicClassifier(
                    api_key=settings.anthropic_api_key,
                    model=settings.anthropic_model,
                )
            except ImportError:
                logger.warning("anthropic package not installed; run: uv add anthropic")
    elif provider == "openai":
        from shared.llm.openai_provider import OpenAICompatClassifier

        instance = OpenAICompatClassifier(
            base_url=settings.openai_compat_base_url,
            model=settings.openai_compat_model,
            api_key=settings.openai_compat_api_key,
        )
    else:
        logger.warning(
            "Unknown CLASSIFIER_LLM_PROVIDER=%r; classification stays heuristic",
            provider,
        )

    _PROVIDER_CACHE[provider] = instance
    return instance


def get_summarizer_provider(settings: Settings) -> SummarizerProvider | None:
    provider = (settings.summarizer_llm_provider or "none").strip().lower()
    if provider in ("", "none"):
        return None
    if provider in _SUMMARIZER_CACHE:
        return _SUMMARIZER_CACHE[provider]

    instance: SummarizerProvider | None = None
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("SUMMARIZER_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY unset")
        else:
            try:
                from shared.llm.anthropic_provider import AnthropicSummarizer

                instance = AnthropicSummarizer(
                    api_key=settings.anthropic_api_key,
                    model=settings.summarizer_model or settings.anthropic_model,
                    max_input_chars=max(
                        settings.summarizer_max_input_chars,
                        settings.summarizer_filings_max_input_chars,
                    ),
                )
            except ImportError:
                logger.warning("anthropic package not installed; run: uv add anthropic")
    elif provider == "openai":
        from shared.llm.openai_provider import OpenAICompatSummarizer

        instance = OpenAICompatSummarizer(
            base_url=settings.openai_compat_base_url,
            model=settings.summarizer_model or settings.openai_compat_model,
            api_key=settings.openai_compat_api_key,
            max_input_chars=max(
                settings.summarizer_max_input_chars,
                settings.summarizer_filings_max_input_chars,
            ),
        )
    else:
        logger.warning("Unknown SUMMARIZER_LLM_PROVIDER=%r; summaries stay off", provider)

    _SUMMARIZER_CACHE[provider] = instance
    return instance


def reset_provider_cache() -> None:
    """Test hook."""
    _PROVIDER_CACHE.clear()
    _SUMMARIZER_CACHE.clear()
