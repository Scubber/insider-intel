"""Resolve ITM detections / preventions from matched techniques."""

from __future__ import annotations

from functools import lru_cache

from shared.itm.index import ItmControlRef, load_itm_index
from shared.schemas.articles import ControlRef, ItmHit

DETECTION_PUBLIC_BASE = "https://insiderthreatmatrix.org/detections"
PREVENTION_PUBLIC_BASE = "https://insiderthreatmatrix.org/preventions"


def detection_public_url(control_id: str) -> str:
    return f"{DETECTION_PUBLIC_BASE}/{control_id.strip()}"


def prevention_public_url(control_id: str) -> str:
    return f"{PREVENTION_PUBLIC_BASE}/{control_id.strip()}"


def _to_control_ref(ref: ItmControlRef) -> ControlRef:
    return ControlRef(id=ref.id, title=ref.title)


def resolve_controls(
    itm_hits: list[ItmHit],
) -> tuple[list[ControlRef], list[ControlRef]]:
    """Union detections/preventions linked to matched techniques (deduped)."""
    if not itm_hits:
        return [], []

    by_id = {tech.id: tech for tech in load_itm_index().techniques}
    detections: list[ControlRef] = []
    preventions: list[ControlRef] = []
    seen_dt: set[str] = set()
    seen_pv: set[str] = set()

    for hit in itm_hits:
        tech = by_id.get(hit.id)
        if tech is None:
            continue
        for ref in tech.detections:
            if ref.id in seen_dt:
                continue
            seen_dt.add(ref.id)
            detections.append(_to_control_ref(ref))
        for ref in tech.preventions:
            if ref.id in seen_pv:
                continue
            seen_pv.add(ref.id)
            preventions.append(_to_control_ref(ref))

    detections.sort(key=lambda c: c.id)
    preventions.sort(key=lambda c: c.id)
    return detections, preventions


@lru_cache(maxsize=1)
def _control_reverse_maps() -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, str],
]:
    """Build detection/prevention → technique ids and id→title catalogs."""
    dt_to_tech: dict[str, list[str]] = {}
    pv_to_tech: dict[str, list[str]] = {}
    dt_titles: dict[str, str] = {}
    pv_titles: dict[str, str] = {}

    for tech in load_itm_index().techniques:
        for ref in tech.detections:
            dt_titles.setdefault(ref.id, ref.title)
            dt_to_tech.setdefault(ref.id, [])
            if tech.id not in dt_to_tech[ref.id]:
                dt_to_tech[ref.id].append(tech.id)
        for ref in tech.preventions:
            pv_titles.setdefault(ref.id, ref.title)
            pv_to_tech.setdefault(ref.id, [])
            if tech.id not in pv_to_tech[ref.id]:
                pv_to_tech[ref.id].append(tech.id)

    return (
        {k: tuple(v) for k, v in dt_to_tech.items()},
        {k: tuple(v) for k, v in pv_to_tech.items()},
        dt_titles,
        pv_titles,
    )


def clear_control_maps_cache() -> None:
    _control_reverse_maps.cache_clear()


def techniques_for_detection(detection_id: str) -> list[str]:
    maps, _, _, _ = _control_reverse_maps()
    return list(maps.get(detection_id.strip().upper(), ()))


def techniques_for_prevention(prevention_id: str) -> list[str]:
    _, maps, _, _ = _control_reverse_maps()
    return list(maps.get(prevention_id.strip().upper(), ()))


def list_detection_catalog() -> list[ControlRef]:
    _, _, titles, _ = _control_reverse_maps()
    return [
        ControlRef(id=cid, title=title)
        for cid, title in sorted(titles.items(), key=lambda item: item[0])
    ]


def list_prevention_catalog() -> list[ControlRef]:
    _, _, _, titles = _control_reverse_maps()
    return [
        ControlRef(id=cid, title=title)
        for cid, title in sorted(titles.items(), key=lambda item: item[0])
    ]
