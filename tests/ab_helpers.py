"""Synthetic-fixture builders shared by the thinking-A/B harness tests."""

from __future__ import annotations

from datetime import UTC, datetime

from shared.schemas import ProcessedArticle
from shared.schemas.forensics import CaseMethod, EnrichmentRecord, PerCaseForensics


def make_forensics(
    link: str,
    title: str,
    *,
    insider: bool = True,
    methods_n: int = 3,
    confidence: float = 0.8,
    posture: str = "indictment",
    model: str = "qwen-local",
    quote: str = "",
    hunt_terms: list[str] | None = None,
    extracted_at: datetime | None = None,
) -> PerCaseForensics:
    methods = [
        CaseMethod(action=f"action {i} for {link}", evidence_quote=quote) for i in range(methods_n)
    ]
    return PerCaseForensics(
        link=link,
        title=title,
        legal_posture=posture,
        methods=methods,
        is_insider_case=insider,
        confidence=confidence,
        model=model,
        hunt_terms=hunt_terms if hunt_terms is not None else (["usb"] if insider else []),
        extracted_at=extracted_at or datetime(2026, 8, 1, tzinfo=UTC),
    )


def make_row(
    link: str,
    *,
    text_chars: int = 6_000,
    insider: bool = True,
    methods_n: int = 3,
    posture: str = "indictment",
    enriched: bool = True,
    baseline_model: str | None = None,
    baseline_insider: bool | None = None,
    source_id: str = "courtlistener-recap",
    clean_text: str | None = None,
) -> ProcessedArticle:
    title = f"Case {link}"
    if clean_text is not None:
        text = clean_text
    else:
        filler = "insider evidence text. "
        text = (filler * (text_chars // len(filler) + 1))[:text_chars]
    forensics = (
        make_forensics(link, title, insider=insider, methods_n=methods_n, posture=posture)
        if enriched
        else None
    )
    history = []
    if forensics is not None:
        history.append(EnrichmentRecord(ai_summary="note", forensics=forensics))
    if baseline_model:
        history.append(
            EnrichmentRecord(
                ai_summary="baseline note",
                forensics=make_forensics(
                    link,
                    title,
                    insider=insider if baseline_insider is None else baseline_insider,
                    methods_n=4,
                    posture=posture,
                    model=baseline_model,
                    extracted_at=datetime(2026, 7, 1, tzinfo=UTC),
                ),
            )
        )
    return ProcessedArticle(
        title=title,
        link=link,
        source_id=source_id,
        source_name="CourtListener",
        clean_text=text,
        forensics=forensics,
        ai_summary="note" if enriched else None,
        enrichment_history=history,
    )


class FakeSummarizer:
    """Stand-in for OpenAICompatSummarizer in runner tests.

    ``replies`` maps case title -> raw reply dict (or None for a parse
    failure); ``default_reply`` covers unmapped titles; titles in
    ``raise_titles`` raise instead. ``last_usage`` mirrors the provider
    usage side-channel the runner reads for non-OpenAICompat providers.
    """

    def __init__(
        self,
        *,
        default_reply: dict | None = None,
        replies: dict[str, dict | None] | None = None,
        raise_titles: tuple[str, ...] = (),
        model_name: str = "fake/qwen",
        last_usage: dict | None = None,
    ) -> None:
        self.default_reply = default_reply
        self.replies = replies or {}
        self.raise_titles = raise_titles
        self.model_name = model_name
        self.last_usage = last_usage
        self.calls: list[str] = []

    def extract_case(self, *, title: str, source: str, text: str, itm_candidates: str):
        self.calls.append(title)
        if title in self.raise_titles:
            raise RuntimeError("provider down")
        if title in self.replies:
            return self.replies[title]
        return self.default_reply
