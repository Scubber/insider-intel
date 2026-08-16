"""Enricher provenance labels: known ids, unknown-id fallback, None-safety."""

from __future__ import annotations

import pytest

from shared.utils.model_display import enricher_display_name


@pytest.mark.parametrize(
    ("model_id", "label"),
    [
        ("Qwen/Qwen3.8-27B-FP8", "Qwen 3.8 27B (local)"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("grok-4", "Grok 4"),
        ("gpt-4o", "GPT-4o"),
    ],
)
def test_known_ids_get_curated_labels(model_id: str, label: str) -> None:
    assert enricher_display_name(model_id) == label


def test_known_ids_match_case_insensitively() -> None:
    # Served ids come back with whatever casing the backend reports.
    assert enricher_display_name("qwen/qwen3.8-27b-fp8") == "Qwen 3.8 27B (local)"
    assert enricher_display_name("Claude-Sonnet-5") == "Claude Sonnet 5"


def test_unknown_claude_id_strips_date_and_formats_version() -> None:
    # A future Anthropic id must not render as raw plumbing.
    assert enricher_display_name("claude-opus-4-1-20250805") == "Claude Opus 4.1"
    assert enricher_display_name("claude-opus-5") == "Claude Opus 5"


def test_unknown_org_prefixed_id_strips_org() -> None:
    assert enricher_display_name("mistralai/mistral-large") == "Mistral Large"
    # Mixed-case tokens (sizes, quant tags) survive as-is.
    assert enricher_display_name("meta-llama/Llama-4-70B") == "Llama 4 70B"


def test_none_and_empty_are_omitted_not_rendered() -> None:
    # Old rows may lack forensics.model entirely — the stamp is omitted;
    # the UI must never be handed "None".
    assert enricher_display_name(None) is None
    assert enricher_display_name("") is None
    assert enricher_display_name("   ") is None
    assert enricher_display_name(None) != "None"
