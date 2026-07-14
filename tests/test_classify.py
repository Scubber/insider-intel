"""Use-case + insider-type heuristic classification and LLM merge behavior."""

from __future__ import annotations

import httpx
import pytest

from shared.llm.base import ClassificationResult
from shared.llm.openai_provider import OpenAICompatClassifier, _parse_result
from shared.utils.classify import classify_insider_type, classify_use_cases
from shared.utils.entities import extract_entities

OE_CONFESSION = """
I've been overemployed for a year now, working two remote jobs (J1 and J2)
without telling either employer. My company has an outside employment policy
but honestly everyone does it. I use a mouse jiggler to stay green on Teams.
"""

REVENGE_EXFIL = """
A disgruntled engineer stole data before resigning: he copied files to a
personal Google Drive and took the source code, planning revenge on the
company and to sell the data to a competitor.
"""

PHISHING_VICTIM = """
An employee fell for a phishing email and unknowingly entered their
credentials on a fake login page; the account was used to reach payroll.
"""

BENIGN = """
The quarterly all-hands covered the roadmap and a new office opening.
Sales were up and the CEO thanked the support team.
"""


def test_oe_confession_classifies_overemployment_negligent() -> None:
    entities = extract_entities(OE_CONFESSION)
    assert "overemployment" in classify_use_cases(OE_CONFESSION, entities)
    assert classify_insider_type(OE_CONFESSION, entities) == "negligent"


def test_revenge_exfil_classifies_data_exfiltration_malicious() -> None:
    entities = extract_entities(REVENGE_EXFIL)
    assert "data-exfiltration" in classify_use_cases(REVENGE_EXFIL, entities)
    assert classify_insider_type(REVENGE_EXFIL, entities) == "malicious"


def test_phishing_victim_classifies_unintentional() -> None:
    assert classify_insider_type(PHISHING_VICTIM, None) == "unintentional"


def test_benign_text_gets_no_labels() -> None:
    entities = extract_entities(BENIGN)
    assert classify_use_cases(BENIGN, entities) == []
    assert classify_insider_type(BENIGN, entities) is None


def test_malicious_outranks_negligent() -> None:
    text = "He was careless about policy but ultimately stole data for revenge."
    assert classify_insider_type(text, None) == "malicious"


def test_weak_keywords_need_two_hits() -> None:
    # A single generic phrase like "side hustle" alone must not fire.
    text = "Thinking about a side hustle selling pottery on weekends."
    assert classify_use_cases(text, None) == []


def test_credential_misuse_and_shadow_it_keywords() -> None:
    cred = "My coworker shared his password with a contractor who still had access."
    assert "credential-misuse" in classify_use_cases(cred, None)
    shadow = "I pasted company data into ChatGPT on my personal laptop for work."
    assert "shadow-it" in classify_use_cases(shadow, None)


def test_empty_text_is_safe() -> None:
    assert classify_use_cases("", None) == []
    assert classify_insider_type("   ", None) is None


# --- Processor gating -------------------------------------------------------


class _StubProvider:
    def __init__(self, result: ClassificationResult | None) -> None:
        self.result = result
        self.calls: list[str] = []

    def classify(self, *, title: str, text: str) -> ClassificationResult | None:
        self.calls.append(title)
        return self.result


def _social_raw(**overrides):
    from shared.schemas import RawArticle

    data = {
        "title": "Vague workplace story",
        "link": "https://www.reddit.com/r/jobsearchhacks/comments/zzz/vague/",
        "summary": "Something odd happened with a coworker's laptop last week.",
        "source_id": "social-reddit-jobsearchhacks",
        "source_name": "Reddit r/jobsearchhacks",
        "channel": "social",
    }
    data.update(overrides)
    return RawArticle.model_validate(data)


def test_llm_refines_thin_heuristics(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agents.article_processor as processor
    from shared.agents import process_article

    stub = _StubProvider(
        ClassificationResult(
            use_cases=["shadow-it"], insider_type="negligent", confidence=0.9
        )
    )
    monkeypatch.setattr(processor, "get_classifier_provider", lambda settings: stub)
    processed = process_article(_social_raw())
    assert stub.calls  # heuristics found nothing -> LLM consulted
    assert processed.use_cases == ["shadow-it"]
    assert processed.insider_type == "negligent"
    assert processed.classification_source == "llm"
    assert processed.classification_confidence == 0.9


def test_low_confidence_llm_keeps_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agents.article_processor as processor
    from shared.agents import process_article

    stub = _StubProvider(
        ClassificationResult(use_cases=["shadow-it"], insider_type=None, confidence=0.3)
    )
    monkeypatch.setattr(processor, "get_classifier_provider", lambda settings: stub)
    processed = process_article(_social_raw())
    assert processed.use_cases == []
    assert processed.classification_source is None


def test_llm_skipped_for_non_social_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agents.article_processor as processor
    from shared.agents import process_article

    stub = _StubProvider(
        ClassificationResult(use_cases=["shadow-it"], insider_type="negligent", confidence=0.9)
    )
    monkeypatch.setattr(processor, "get_classifier_provider", lambda settings: stub)
    process_article(
        _social_raw(
            link="https://example.com/news-item",
            source_id="example",
            source_name="Example",
            channel="news",
        )
    )
    assert stub.calls == []  # default CLASSIFY_LLM_CHANNELS=social


def test_llm_error_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agents.article_processor as processor
    from shared.agents import process_article

    class ExplodingProvider:
        def classify(self, *, title: str, text: str):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        processor, "get_classifier_provider", lambda settings: ExplodingProvider()
    )
    processed = process_article(_social_raw())
    assert processed.use_cases == []
    assert processed.insider_type is None


# --- LLM plumbing -----------------------------------------------------------


def test_parse_result_strict_json() -> None:
    result = _parse_result(
        '{"use_cases": ["overemployment"], "insider_type": "negligent",'
        ' "confidence": 0.9}'
    )
    assert result is not None
    assert result.use_cases == ["overemployment"]
    assert result.insider_type == "negligent"


def test_parse_result_extracts_embedded_json_and_sanitizes() -> None:
    reply = (
        'Sure! {"use_cases": ["overemployment", "made-up-case"],'
        ' "insider_type": null, "confidence": 0.7}'
    )
    result = _parse_result(reply)
    assert result is not None
    assert result.use_cases == ["overemployment"]
    assert result.insider_type is None


def test_parse_result_garbage_returns_none() -> None:
    assert _parse_result("no json here") is None
    assert _parse_result('{"insider_type": "sideways"}') is None


def test_openai_provider_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs) -> httpx.Response:
        assert url.endswith("/chat/completions")
        body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"use_cases": ["shadow-it"],'
                            ' "insider_type": "negligent", "confidence": 0.8}'
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatClassifier(base_url="http://localhost:11434/v1", model="m")
    result = provider.classify(title="t", text="x")
    assert result == ClassificationResult(
        use_cases=["shadow-it"], insider_type="negligent", confidence=0.8
    )


def test_openai_provider_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatClassifier(base_url="http://localhost:11434/v1", model="m")
    assert provider.classify(title="t", text="x") is None
