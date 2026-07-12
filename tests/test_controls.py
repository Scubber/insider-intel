"""Tests for technique → detection/prevention control join."""

from __future__ import annotations

from shared.itm.controls import resolve_controls
from shared.itm.index import clear_itm_cache, load_itm_index
from shared.schemas.articles import ItmHit
from shared.utils.entities import extract_entities


def test_index_techniques_carry_control_refs() -> None:
    clear_itm_cache()
    index = load_itm_index()
    with_dt = [t for t in index.techniques if t.detections]
    with_pv = [t for t in index.techniques if t.preventions]
    assert with_dt, "itm_index.json should retain technique→DT links after refresh"
    assert with_pv, "itm_index.json should retain technique→PV links after refresh"
    sample = with_dt[0]
    assert sample.detections[0].id.startswith("DT")
    assert sample.detections[0].title


def test_resolve_controls_dedupes_across_hits() -> None:
    clear_itm_cache()
    tech = next(t for t in load_itm_index().techniques if t.detections)
    hits = [
        ItmHit(
            id=tech.id,
            title=tech.title,
            theme=tech.theme,
            article_id=tech.article_id,
            matched_aliases=[tech.title.lower()],
        ),
        ItmHit(
            id=tech.id,
            title=tech.title,
            theme=tech.theme,
            article_id=tech.article_id,
            matched_aliases=["dup"],
        ),
    ]
    detections, preventions = resolve_controls(hits)
    assert len(detections) == len({c.id for c in detections})
    assert {c.id for c in detections} == {c.id for c in tech.detections}


def test_extract_entities_joins_controls_not_dt_text() -> None:
    """News text lacks Event IDs; join still yields DTs via technique aliases."""
    entities = extract_entities(
        "An insider threat case described USB exfiltration by a departing employee "
        "using removable media after resignation."
    )
    assert entities.itm_hits
    assert entities.related_detections
    # Article body never mentions a DT id; join is technique-driven
    assert "DT021" not in (
        "An insider threat case described USB exfiltration by a departing employee "
        "using removable media after resignation."
    )
