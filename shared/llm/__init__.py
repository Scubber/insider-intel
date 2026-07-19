"""Optional LLM refiner for use-case / insider-type classification.

Provider is chosen via CLASSIFIER_LLM_PROVIDER (none | anthropic | openai | gemini);
"openai" means any OpenAI-compatible endpoint — Ollama/vLLM/LM Studio for
local models, or a hosted service via OPENAI_COMPAT_BASE_URL.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from shared.llm.base import (
    ClassificationResult,
    ClassifierProvider,
    DiscovererProvider,
    ItmRef,
    SummarizerProvider,
)

if TYPE_CHECKING:
    from shared.settings import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "ClassificationResult",
    "ClassifierProvider",
    "DiscovererProvider",
    "ItmRef",
    "SummarizerProvider",
    "get_classifier_provider",
    "get_discoverer_chain",
    "get_discoverer_provider",
    "get_summarizer_chain",
    "get_summarizer_provider",
]

_PROVIDER_CACHE: dict[str, ClassifierProvider | None] = {}
_SUMMARIZER_CACHE: dict[str, SummarizerProvider | None] = {}
_DISCOVERER_CACHE: dict[str, DiscovererProvider | None] = {}
_SUMMARIZER_CHAIN_CACHE: dict[str, list] = {}
_DISCOVERER_CHAIN_CACHE: dict[str, list] = {}

_OPENAI_COMPAT_DEFAULT_BASE = "http://localhost:11434/v1"
_OPENAI_COMPAT_DEFAULT_MODEL = "llama3.1:8b"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


def resolve_openai_compat(settings: Settings) -> tuple[str, str, str | None]:
    """(base_url, model, api_key) for the openai provider.

    A bare OPENAI_API_KEY means "use real OpenAI": the local-Ollama defaults
    for base URL and model are swapped for api.openai.com + a cheap model,
    while explicit OPENAI_COMPAT_* values always win.
    """
    base_url = settings.openai_compat_base_url
    model = settings.openai_compat_model
    api_key = settings.openai_compat_api_key or settings.openai_api_key
    if settings.openai_api_key and not settings.openai_compat_api_key:
        if base_url.rstrip("/") == _OPENAI_COMPAT_DEFAULT_BASE:
            base_url = OPENAI_DEFAULT_BASE
        if model == _OPENAI_COMPAT_DEFAULT_MODEL:
            model = OPENAI_DEFAULT_MODEL
    return base_url, model, api_key


def resolve_gemini_compat(settings: Settings) -> tuple[str, str, str | None]:
    """(base_url, model, api_key) for Gemini via its OpenAI-compatible API."""
    return GEMINI_OPENAI_BASE, settings.gemini_model, settings.gemini_api_key


def _resolve_provider(
    name: str, settings: Settings, model_override: str | None
) -> tuple[str, str | None, str, str | None] | None:
    """Resolve one chain entry → (kind, base_url, model, api_key), or None.

    ``kind`` is "anthropic" or "openai_compat". Returns None when the provider
    can't be built (missing key / unknown name / malformed custom entry) so the
    chain simply skips it. ``openai`` always builds (it may be a local endpoint);
    anthropic/gemini/custom require their key.
    """
    name = name.lower()
    if name == "anthropic":
        if not settings.anthropic_api_key:
            return None
        return (
            "anthropic",
            None,
            model_override or settings.anthropic_model,
            settings.anthropic_api_key,
        )
    if name == "openai":
        base_url, model, api_key = resolve_openai_compat(settings)
        return ("openai_compat", base_url, model_override or model, api_key)
    if name == "gemini":
        if not settings.gemini_api_key:
            return None
        base_url, model, api_key = resolve_gemini_compat(settings)
        return ("openai_compat", base_url, model_override or model, api_key)
    custom = settings.custom_llm_provider_map().get(name)
    if custom:
        base_url = str(custom.get("base_url") or "").strip()
        model = model_override or str(custom.get("model") or "").strip()
        if not base_url or not model:
            logger.warning("Custom LLM provider %r missing base_url/model; skipped", name)
            return None
        key_env = str(custom.get("api_key_env") or "").strip()
        api_key = os.environ.get(key_env) if key_env else custom.get("api_key")
        return ("openai_compat", base_url, model, api_key)
    logger.warning("Unknown LLM provider %r in chain; skipped", name)
    return None


def _build_summarizer(
    name: str, settings: Settings, model_override: str | None
) -> SummarizerProvider | None:
    resolved = _resolve_provider(name, settings, model_override)
    if resolved is None:
        return None
    kind, base_url, model, api_key = resolved
    max_chars = max(
        settings.summarizer_max_input_chars, settings.summarizer_filings_max_input_chars
    )
    if kind == "anthropic":
        try:
            from shared.llm.anthropic_provider import AnthropicSummarizer
        except ImportError:
            logger.warning("anthropic package not installed; run: uv add anthropic")
            return None
        return AnthropicSummarizer(api_key=api_key, model=model, max_input_chars=max_chars)
    from shared.llm.openai_provider import OpenAICompatSummarizer

    return OpenAICompatSummarizer(
        base_url=base_url, model=model, api_key=api_key, max_input_chars=max_chars
    )


def _build_discoverer(
    name: str, settings: Settings, model_override: str | None
) -> DiscovererProvider | None:
    resolved = _resolve_provider(name, settings, model_override)
    if resolved is None:
        return None
    kind, base_url, model, api_key = resolved
    if kind == "anthropic":
        try:
            from shared.llm.anthropic_provider import AnthropicDiscoverer
        except ImportError:
            logger.warning("anthropic package not installed; run: uv add anthropic")
            return None
        return AnthropicDiscoverer(api_key=api_key, model=model)
    from shared.llm.openai_provider import OpenAICompatDiscoverer

    return OpenAICompatDiscoverer(base_url=base_url, model=model, api_key=api_key)


def get_summarizer_chain(settings: Settings) -> list[SummarizerProvider]:
    """Ordered enrichment providers, tried until one succeeds.

    A role-level ``summarizer_model`` override applies to the primary (first)
    provider only; fallbacks use their provider-default model. Providers that
    can't be built (missing key) are dropped, so an unfunded chain entry is
    harmless.
    """
    chain = settings.summarizer_provider_chain()
    cache_key = "|".join(chain) + "::" + (settings.summarizer_model or "")
    if cache_key in _SUMMARIZER_CHAIN_CACHE:
        return _SUMMARIZER_CHAIN_CACHE[cache_key]
    providers: list[SummarizerProvider] = []
    for i, name in enumerate(chain):
        override = settings.summarizer_model if i == 0 else None
        provider = _build_summarizer(name, settings, override)
        if provider is not None:
            providers.append(provider)
    _SUMMARIZER_CHAIN_CACHE[cache_key] = providers
    return providers


def get_discoverer_chain(settings: Settings) -> list[DiscovererProvider]:
    """Ordered discovery providers (inherits the summarizer chain when unset)."""
    chain = settings.discoverer_provider_chain()
    model_override = settings.discoverer_model or settings.summarizer_model
    cache_key = "|".join(chain) + "::" + (model_override or "")
    if cache_key in _DISCOVERER_CHAIN_CACHE:
        return _DISCOVERER_CHAIN_CACHE[cache_key]
    providers: list[DiscovererProvider] = []
    for i, name in enumerate(chain):
        override = model_override if i == 0 else None
        provider = _build_discoverer(name, settings, override)
        if provider is not None:
            providers.append(provider)
    _DISCOVERER_CHAIN_CACHE[cache_key] = providers
    return providers


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

        base_url, model, api_key = resolve_openai_compat(settings)
        instance = OpenAICompatClassifier(base_url=base_url, model=model, api_key=api_key)
    elif provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning("CLASSIFIER_LLM_PROVIDER=gemini but GEMINI_API_KEY unset")
        else:
            from shared.llm.openai_provider import OpenAICompatClassifier

            base_url, model, api_key = resolve_gemini_compat(settings)
            instance = OpenAICompatClassifier(base_url=base_url, model=model, api_key=api_key)
    else:
        logger.warning(
            "Unknown CLASSIFIER_LLM_PROVIDER=%r; classification stays heuristic",
            provider,
        )

    _PROVIDER_CACHE[provider] = instance
    return instance


def get_summarizer_provider(settings: Settings) -> SummarizerProvider | None:
    """Primary enrichment provider (head of the chain), or None if unconfigured.

    Kept for callers that only need the on/off gate; the actual per-call fallback
    iterates ``get_summarizer_chain``.
    """
    chain = get_summarizer_chain(settings)
    return chain[0] if chain else None


def get_discoverer_provider(settings: Settings) -> DiscovererProvider | None:
    """Primary discovery provider (head of the chain), or None if unconfigured."""
    chain = get_discoverer_chain(settings)
    return chain[0] if chain else None


def reset_provider_cache() -> None:
    """Test hook."""
    _PROVIDER_CACHE.clear()
    _SUMMARIZER_CACHE.clear()
    _DISCOVERER_CACHE.clear()
    _SUMMARIZER_CHAIN_CACHE.clear()
    _DISCOVERER_CHAIN_CACHE.clear()
