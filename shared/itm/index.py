"""Load and query the slim Insider Threat Matrix™ index."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shared.itm.aliases import CURATED_ALIASES

DEFAULT_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "itm_index.json"


class ItmArticleMeta(BaseModel):
    id: str
    title: str
    theme: str


class ItmControlRef(BaseModel):
    """Slim reference to an ITM Detection (DT*) or Prevention (PV*)."""

    id: str
    title: str


class ItmTechnique(BaseModel):
    id: str
    title: str
    article_id: str
    theme: str
    parent_id: str | None = None
    description_text: str = ""
    aliases: list[str] = Field(default_factory=list)
    detections: list[ItmControlRef] = Field(default_factory=list)
    preventions: list[ItmControlRef] = Field(default_factory=list)


class ItmIndex(BaseModel):
    itm_version: str | None = None
    mitre_version: str | None = None
    refreshed_at: str | None = None
    source_url: str | None = None
    articles: list[ItmArticleMeta] = Field(default_factory=list)
    techniques: list[ItmTechnique] = Field(default_factory=list)


def get_itm_index_path() -> Path:
    return DEFAULT_INDEX_PATH


def _load_raw(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _merge_curated_aliases(techniques: list[ItmTechnique]) -> list[ItmTechnique]:
    """Ensure runtime curated aliases are present even before refresh_itm."""
    merged: list[ItmTechnique] = []
    for tech in techniques:
        aliases = list(tech.aliases)
        seen = {a.lower() for a in aliases}
        for extra in CURATED_ALIASES.get(tech.id, ()):
            cleaned = extra.strip().lower()
            if cleaned and cleaned not in seen:
                aliases.append(cleaned)
                seen.add(cleaned)
        merged.append(tech.model_copy(update={"aliases": aliases}))
    return merged


@lru_cache(maxsize=4)
def load_itm_index(path: str | None = None) -> ItmIndex:
    """Load the slim ITM index (cached). Pass path=None for the packaged default."""
    resolved = Path(path) if path else get_itm_index_path()
    if not resolved.is_file():
        return ItmIndex()
    index = ItmIndex.model_validate(_load_raw(resolved))
    return index.model_copy(update={"techniques": _merge_curated_aliases(index.techniques)})


def clear_itm_cache() -> None:
    from shared.itm.controls import clear_control_maps_cache

    load_itm_index.cache_clear()
    clear_control_maps_cache()


def list_articles(path: str | None = None) -> list[ItmArticleMeta]:
    return list(load_itm_index(path).articles)


def list_techniques(path: str | None = None) -> list[ItmTechnique]:
    return list(load_itm_index(path).techniques)
