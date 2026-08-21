"""OpenAI-compatible chat-completions providers (Ollama, vLLM, LM Studio, xAI...)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import NamedTuple

import httpx

from shared.llm.base import (
    CLASSIFY_SYSTEM_PROMPT,
    DISCOVER_SYSTEM_PROMPT,
    ENRICH_REPLY_SCHEMA,
    ENRICH_SYSTEM_PROMPT,
    SYNTH_SYSTEM_PROMPT,
    ClassificationResult,
    build_discover_prompt,
    build_enrich_prompt,
    build_synth_prompt,
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
# Hunt synthesis returns 2-4 compact patterns per technique.
SYNTH_MAX_TOKENS = 3000

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_AUTO_MODEL_TOKENS = frozenset({"", "auto", "*"})
_SERVED_MODEL_CACHE: dict[str, str] = {}
_PROBE_BACKOFF_SECONDS = 2.0


class ChatResult(NamedTuple):
    content: str
    model: str | None = None


# Usage block ({prompt,completion,total}_tokens) from the most recent chat
# completion on this base_url stack, or None. Observability side-channel for
# the thinking A/B harness (scripts/ab_thinking_run.py): the provider
# contracts return only the parsed JSON reply, so per-call token counts are
# surfaced here instead of widening every Protocol. Reset at the start of
# each call; read it immediately after the call returns. Deliberately not
# thread-safe — the callers that read it are sequential eval loops.
_LAST_USAGE: dict | None = None


def get_last_usage() -> dict | None:
    """Usage dict from the most recent chat completion, or None."""
    return _LAST_USAGE


def reset_served_model_cache() -> None:
    """Test hook — GET /v1/models is cached per base_url."""
    _SERVED_MODEL_CACHE.clear()


def is_auto_model(model: str | None) -> bool:
    return (model or "").strip().lower() in _AUTO_MODEL_TOKENS


def discover_openai_compat_model(
    base_url: str, api_key: str | None, *, timeout: float = 10.0, attempts: int = 3
) -> str | None:
    """Return the first id from GET /v1/models, or None.

    Used when a custom provider sets ``model`` to ``auto`` (or omits it) so a
    host can swap vLLM weights without retuning tenant config. The completion
    response ``model`` field still wins for ``forensics.model`` tagging.

    A None here makes the chain builder drop the provider — on a local-only
    chain that silently zeroes enrichment for the run — so transient failures
    (server restart, checkpoint still loading) get retried with backoff and
    the terminal failure is a greppable [FAIL] line, never just a warning.
    """
    base = base_url.rstrip("/")
    cached = _SERVED_MODEL_CACHE.get(base)
    if cached:
        return cached
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(_PROBE_BACKOFF_SECONDS * attempt)
        try:
            response = httpx.get(f"{base}/models", headers=headers, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            rows = body.get("data") if isinstance(body, dict) else None
            # A well-formed reply with no usable id is a config problem, not a
            # transient one — retrying can't fix the body shape.
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                logger.error(
                    "[FAIL] llm-probe: GET %s/models returned an unexpected body: %.200r",
                    base,
                    body,
                )
                return None
            mid = str(rows[0].get("id") or "").strip()
            if not mid:
                logger.error("[FAIL] llm-probe: GET %s/models first row has no id", base)
                return None
            _SERVED_MODEL_CACHE[base] = mid
            logger.info("OpenAI-compat %s serves %s", base, mid)
            return mid
        except (
            httpx.HTTPError,
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            logger.warning(
                "OpenAI-compat GET /models attempt %d/%d failed for %s: %s",
                attempt + 1,
                attempts,
                base,
                exc,
            )
    logger.error(
        "[FAIL] llm-probe: GET %s/models failed after %d attempts: %s",
        base,
        attempts,
        last_error,
    )
    return None


def _chat_completion(
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
    system: str,
    user: str,
    max_tokens: int | None = None,
    enable_thinking: bool = True,
    json_schema: dict | None = None,
) -> ChatResult | None:
    """POST a JSON-mode chat completion; returns content + served model, or None.

    With ``json_schema``, decoding is grammar-enforced (vLLM structured
    output): the model structurally cannot omit required keys or emit
    malformed JSON. Fallback ladder on server rejection: json_schema ->
    json_object -> no response_format.
    """
    global _LAST_USAGE
    _LAST_USAGE = None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response_format: dict = {"type": "json_object"}
    if json_schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "enrichment_reply", "schema": json_schema},
        }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": response_format,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if not enable_thinking:
        # vLLM/Qwen3-style servers; key is absent entirely when thinking is on,
        # so non-vLLM payloads stay byte-identical to pre-knob behavior.
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    url = f"{base_url}/chat/completions"
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        if (
            response.status_code == 400
            and json_schema is not None
            and ("json_schema" in response.text or "response_format" in response.text)
        ):
            # Schema tier rejected (older server / grammar backend off):
            # downgrade to plain JSON mode and retry once. 400s naming other
            # keys fall through to the generic strip-and-retry below.
            logger.warning("Server rejected json_schema response_format; retrying as json_object")
            payload["response_format"] = {"type": "json_object"}
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        if response.status_code == 400:
            # Some servers reject response_format or chat_template_kwargs by
            # name; strip whichever the error mentions and retry once.
            stripped = False
            for key in ("response_format", "chat_template_kwargs"):
                if key in payload and key in response.text:
                    payload.pop(key, None)
                    stripped = True
            if stripped:
                response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage")
        _LAST_USAGE = usage if isinstance(usage, dict) else None
        served = body.get("model")
        served_model = served.strip() if isinstance(served, str) and served.strip() else None
        return ChatResult(content=content, model=served_model)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("OpenAI-compat chat call failed: %s", exc)
        return None


def _content_and_tag(owner: object, result: ChatResult | None) -> str | None:
    """Return reply text and stamp owner.model_name from the server when present."""
    if result is None:
        return None
    if result.model and hasattr(owner, "model_name"):
        owner.model_name = result.model
    return result.content


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
        content = _content_and_tag(
            self,
            _chat_completion(
                base_url=self._base_url,
                model=self._model,
                api_key=self._api_key,
                timeout=self._timeout,
                system=CLASSIFY_SYSTEM_PROMPT,
                user=build_user_prompt(title, text),
            ),
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
        enable_thinking: bool = True,
        guided_json: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_input_chars = max_input_chars
        self._timeout = timeout
        self._enable_thinking = enable_thinking
        self._guided_json = guided_json
        self.model_name = model

    def extract_case(
        self, *, title: str, source: str, text: str, itm_candidates: str
    ) -> dict | None:
        content = _content_and_tag(
            self,
            _chat_completion(
                base_url=self._base_url,
                model=self._model,
                api_key=self._api_key,
                timeout=self._timeout,
                enable_thinking=self._enable_thinking,
                system=ENRICH_SYSTEM_PROMPT,
                user=build_enrich_prompt(
                    title=title,
                    source=source,
                    text=text,
                    itm_candidates=itm_candidates,
                    max_chars=self._max_input_chars,
                ),
                max_tokens=ENRICH_MAX_TOKENS,
                json_schema=ENRICH_REPLY_SCHEMA if self._guided_json else None,
            ),
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
        enable_thinking: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_input_chars = max_input_chars
        self._timeout = timeout
        self._enable_thinking = enable_thinking
        self.model_name = model

    def discover_techniques(self, *, forensics_json: str, itm_shortlist: str) -> dict | None:
        content = _content_and_tag(
            self,
            _chat_completion(
                base_url=self._base_url,
                model=self._model,
                api_key=self._api_key,
                timeout=self._timeout,
                enable_thinking=self._enable_thinking,
                system=DISCOVER_SYSTEM_PROMPT,
                user=build_discover_prompt(
                    forensics_json=forensics_json,
                    itm_shortlist=itm_shortlist,
                    max_chars=self._max_input_chars,
                ),
                max_tokens=DISCOVER_MAX_TOKENS,
            ),
        )
        if content is None:
            return None
        return _parse_json_object(content, label="Discoverer")


class OpenAICompatSynthesizer:
    """Corpus-level hunt-pattern synthesis for one technique's case material."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 90.0,
        enable_thinking: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._enable_thinking = enable_thinking
        self.model_name = model

    def synthesize_hunts(self, *, technique_json: str) -> dict | None:
        content = _content_and_tag(
            self,
            _chat_completion(
                base_url=self._base_url,
                model=self._model,
                api_key=self._api_key,
                timeout=self._timeout,
                enable_thinking=self._enable_thinking,
                system=SYNTH_SYSTEM_PROMPT,
                user=build_synth_prompt(technique_json=technique_json),
                max_tokens=SYNTH_MAX_TOKENS,
            ),
        )
        if content is None:
            return None
        return _parse_json_object(content, label="Synthesizer")


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
