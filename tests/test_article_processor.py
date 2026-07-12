"""Tests for the LangGraph article processor."""

from __future__ import annotations

from pathlib import Path

from apps.aggregator.process_pipeline import run_processing
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
from shared.schemas import RawArticle


def _raw(**overrides: object) -> RawArticle:
    data = {
        "title": "Insider threat: data exfiltration via CVE-2024-11111",
        "link": "https://example.com/insider-alert",
        "summary": (
            "<p>Disgruntled employee used removable media after resignation. "
            "Domain badactor.io observed.</p>"
        ),
        "source_id": "example",
        "source_name": "Example",
    }
    data.update(overrides)
    return RawArticle.model_validate(data)


def test_process_article_extracts_and_scores() -> None:
    processed = process_article(_raw())
    assert "CVE-2024-11111" in processed.entities.cves
    assert "insider threat" in processed.entities.keywords_hit
    assert "data exfiltration" in processed.entities.keywords_hit
    hit_ids = {h.id for h in processed.entities.itm_hits}
    assert "IF001" in hit_ids or "ME005" in hit_ids
    assert "MT003" in hit_ids or any(i.startswith("MT003") for i in hit_ids)
    assert "<p>" not in (processed.summary or "")
    assert processed.relevance_score > 0.2
    assert "Insider threat" in processed.clean_text
    assert processed.embedding is not None
    assert len(processed.embedding) == 256
    assert processed.itm_alignment == "insider"


def test_run_processing_writes_processed_store(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    processed_path = tmp_path / "processed.jsonl"
    store = JsonlArticleStore(raw_path)
    store.save([_raw(), _raw(link="https://example.com/other", title="Generic update")])

    result = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert result.articles_read == 2
    assert result.articles_saved == 2
    assert processed_path.exists()

    # Second run de-dupes
    again = run_processing(raw_path=raw_path, processed_path=processed_path)
    assert again.articles_skipped == 2
    assert again.articles_saved == 0
