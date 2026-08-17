"""Search service singleton used by the API and CLI."""

from __future__ import annotations

import logging
from pathlib import Path

from apps.aggregator.config import DEFAULT_FEEDS
from apps.search.index import ArticleSearchIndex
from shared.itm.index import load_itm_index
from shared.schemas import (
    ArticleListResponse,
    CandidateCatalogResponse,
    ControlRef,
    ItmCatalogResponse,
    SearchMode,
    SearchResponse,
    SocialCatalogResponse,
    SocialSourceInfo,
    SourceInfo,
    UseCaseInfo,
)
from shared.schemas.articles import resolve_channel
from shared.schemas.search import ItmArticleSummary, ItmTechniqueSummary
from shared.settings import get_settings

logger = logging.getLogger(__name__)

_index: ArticleSearchIndex | None = None
_index_path: str | None = None


def get_index(path: str | Path | None = None, *, reload: bool = False) -> ArticleSearchIndex:
    """Return the shared search index, loading from disk if needed."""
    global _index, _index_path

    settings = get_settings()
    resolved = str(path or settings.processed_articles_path)

    if _index is None or reload or _index_path != resolved:
        logger.info("Loading search index from %s", resolved)
        _index = ArticleSearchIndex.from_jsonl(resolved)
        _index_path = resolved
    return _index


def warm_vendor_scan(index: ArticleSearchIndex | None = None) -> None:
    """Pre-warm the per-index vendor-mention scan (the /tooling hot path).

    The scan cache is weak-keyed on the index object
    (apps/search/vendor_mentions.py), so every index swap starts cold and the
    FIRST /tooling request after it would pay the full aliases × corpus scan
    (~164 aliases × 7k docs). /reload calls this right after the swap — its
    only caller is the 6h refresh job, which can afford the extra seconds —
    so the cache is hot before any user request. Best-effort by contract: a
    scan failure is logged and swallowed, never breaking the reload (the
    first /tooling call would simply rescan or surface the error itself).
    """
    from apps.search.vendor_mentions import mentions_for_index

    try:
        mentions_for_index(index if index is not None else get_index())
    except Exception:
        logger.exception("vendor-mention warm scan failed; first /tooling call will rescan")


def list_sources(
    path: str | Path | None = None,
    *,
    min_score: float = 0.0,
    theme: str | None = None,
    itm_id: str | None = None,
    itm_alignment: str = "all",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
) -> list[SourceInfo]:
    """Sources with article counts for the active stream filters.

    When filters are applied, only sources with matching articles are returned
    (counts reflect the filtered set). Unfiltered calls still merge configured feeds.
    """
    configured = {f.id: f for f in DEFAULT_FEEDS}
    indexed = {
        sid: (name, count)
        for sid, name, count in get_index(path).list_sources(
            min_score=min_score,
            theme=theme,
            itm_id=itm_id,
            itm_alignment=itm_alignment,
            channel=channel,
            use_case=use_case,
            insider_type=insider_type,
        )
    }

    filtered = (
        (itm_alignment or "all").strip().lower() not in {"", "all", "*"}
        or bool(theme or itm_id or min_score > 0)
        or (channel or "all").strip().lower() not in {"", "all", "*"}
        or (use_case or "all").strip().lower() not in {"", "all", "*"}
        or (insider_type or "all").strip().lower() not in {"", "all", "*"}
    )

    merged: dict[str, SourceInfo] = {}
    if not filtered:
        for sid, feed in configured.items():
            count = indexed.get(sid, ("", 0))[1]
            merged[sid] = SourceInfo(
                id=feed.id,
                name=feed.name,
                url=str(feed.url),
                category=feed.category,
                channel=resolve_channel(feed.id, feed.channel, category=feed.category),
                enabled=feed.enabled,
                article_count=count,
            )
    for sid, (name, count) in indexed.items():
        if sid in merged:
            merged[sid].article_count = count
            continue
        feed = configured.get(sid)
        merged[sid] = SourceInfo(
            id=sid,
            name=(feed.name if feed else None) or name or sid,
            url=str(feed.url) if feed else None,
            category=(feed.category if feed else None) or "ingested",
            channel=resolve_channel(
                sid,
                feed.channel if feed else None,
                category=(feed.category if feed else None),
            ),
            enabled=feed.enabled if feed else True,
            article_count=count,
        )
    return sorted(merged.values(), key=lambda s: s.name.lower())


def search(
    query: str,
    *,
    mode: SearchMode | str = SearchMode.hybrid,
    limit: int = 10,
    min_score: float = 0.0,
    source_id: str | None = None,
    theme: str | None = None,
    itm_id: str | None = None,
    itm_alignment: str = "insider",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
    path: str | Path | None = None,
) -> SearchResponse:
    if isinstance(mode, str):
        mode = SearchMode(mode)
    index = get_index(path)
    return index.search(
        query,
        mode=mode,
        limit=limit,
        min_score=min_score,
        source_id=source_id,
        theme=theme,
        itm_id=itm_id,
        itm_alignment=itm_alignment,
        channel=channel,
        use_case=use_case,
        insider_type=insider_type,
    )


def _raw_evidence_ledger(path: str | Path | None = None, *, top: int = 25) -> dict:
    from shared.utils.evidence import build_evidence_ledger

    rows = (
        {
            "link": a.link,
            "title": a.title,
            "published": a.published.isoformat() if a.published else "",
            "forensics": a.forensics.model_dump(mode="json") if a.forensics else None,
        }
        for a in get_index(path).articles
    )
    return build_evidence_ledger(rows, top=top)


def _catalog_detections() -> dict[str, list[dict]]:
    """technique id -> [{'id','title'}, ...] from the packaged ITM catalog."""
    return {
        tech.id.upper(): [{"id": d.id, "title": d.title} for d in tech.detections]
        for tech in load_itm_index().techniques
    }


def evidence_ledger(path: str | Path | None = None, *, top: int = 25) -> dict:
    """Corpus-wide evidence ledger from the loaded index (no LLM, no I/O).

    Same aggregation core as the evidence-ledger workflow, then joined against
    the packaged ITM catalog: each top technique's detections get real-case
    corroboration stamps, and the header strip gets the totals ("N of M ITM
    detections across observed techniques corroborated"). Recomputed from the
    in-memory index so it stays current with every /reload.
    """
    from shared.utils.evidence import corroborate_detections

    ledger = _raw_evidence_ledger(path, top=top)
    families = ledger.pop("technique_families", {})
    ledger.pop("technique_counts", {})
    ledger.pop("technique_hunts", {})  # served per-technique, not corpus-wide
    ledger.pop("technique_terms", {})
    ledger.pop("technique_behaviors", {})
    by_tech = _catalog_detections()
    # Spell out technique names for a non-analyst reader (the core stays
    # catalog-free, so the human title is joined in here).
    titles = {t.id.upper(): t.title for t in load_itm_index().techniques}

    corroborated_ids: set[str] = set()
    all_ids: set[str] = set()
    for tech_id in families:
        for det in corroborate_detections(by_tech.get(tech_id, []), families.get(tech_id, {})):
            all_ids.add(det["id"])
            if det["corroborated"]:
                corroborated_ids.add(det["id"])
    for tech in ledger["techniques"]:
        tech["title"] = titles.get(str(tech["id"]).upper(), str(tech["id"]))
        tech["detections"] = corroborate_detections(
            by_tech.get(tech["id"], []), families.get(tech["id"], {})
        )
    ledger["corroboration"] = {
        "detections_in_scope": len(all_ids),
        "corroborated": len(corroborated_ids),
    }
    return ledger


def evidence_technique(tech_id: str, path: str | Path | None = None) -> dict | None:
    """Per-technique evidence detail for the dossier's OBSERVED EVIDENCE section.

    Case-scoped join (evidence seen in cases EXHIBITING the technique — stated
    as such in the UI). Returns None for a technique with no observed cases.
    """
    from shared.utils.evidence import corroborate_detections, technique_theme

    tech_id = (tech_id or "").upper().strip()
    ledger = _raw_evidence_ledger(path, top=1)
    families = ledger.get("technique_families", {}).get(tech_id)
    counts = ledger.get("technique_counts", {}).get(tech_id)
    if not counts:
        return None
    detections = corroborate_detections(_catalog_detections().get(tech_id, []), families or {})
    ranked = sorted((families or {}).items(), key=lambda kv: -kv[1])
    return {
        "id": tech_id,
        "theme": technique_theme(tech_id),
        "cases": counts["cases"],
        "adjudicated_admitted": counts["adjudicated_admitted"],
        "alleged": counts["alleged"],
        "enriched_cases": ledger["enriched_cases"],
        "small_n_floor": ledger["small_n_floor"],
        "detections": detections,
        "evidence": [{"artifact": fam, "cases": n} for fam, n in ranked[:8]],
        "hunts": ledger.get("technique_hunts", {}).get(tech_id, []),
        "terms": ledger.get("technique_terms", {}).get(tech_id, []),
        "behaviors": ledger.get("technique_behaviors", {}).get(tech_id, []),
        **_synthesized_hunts(tech_id),
    }


def _synthesized_hunts(tech_id: str) -> dict:
    """Job-synthesized generalized patterns for the dossier (empty until swept)."""
    from apps.aggregator.hunt_synthesis import TechniqueHuntStore

    entry = TechniqueHuntStore(get_settings().technique_hunts_path).read().get(tech_id)
    if entry is None:
        return {"patterns": [], "patterns_generated_at": None}
    return {
        "patterns": [p.model_dump(mode="json") for p in entry.patterns],
        "patterns_generated_at": entry.generated_at,
    }


def tooling_rankings(path: str | Path | None = None) -> dict:
    """TOOLING page payload: curated tool categories ranked by real-case coverage.

    Taxonomy from shared/data/tooling_map.json (checked in, sweep-stable);
    numbers from the verdict-gated evidence ledger recomputed off the
    in-memory index (per-technique case counts + detected_by record classes),
    so every /reload after a sweep re-ranks on the next call — no snapshot,
    no redeploy. Percentages obey the ledger's small-n law.

    Vendor rows: each category's example vendors are decorated with documented
    case-mention counts (distinct stored documents whose text names the
    product — apps/search/vendor_mentions.py). The scan is computed once per
    index generation (weak-keyed on the index object, so /reload's index swap
    invalidates it) and NEVER enters the category ranking math above.
    """
    from apps.search.tooling import load_tooling_map, rank_tool_categories
    from apps.search.vendor_mentions import attach_vendor_mentions, mentions_for_index
    from shared.utils.evidence import SMALL_N_FLOOR

    ledger = _raw_evidence_ledger(path, top=50)
    catalog = {
        tech.id.upper(): {
            "title": tech.title,
            "detections": [d.id for d in tech.detections],
            "preventions": [p.id for p in tech.preventions],
        }
        for tech in load_itm_index().techniques
    }
    ranked = rank_tool_categories(
        load_tooling_map()["categories"],
        ledger.get("technique_counts", {}),
        catalog,
        ledger.get("detected_by", []),
        suppress_pct=ledger["enriched_cases"] < SMALL_N_FLOOR,
    )
    # Spell out control titles for the page (the ranking core stays id-only).
    from shared.itm.controls import list_detection_catalog, list_prevention_catalog

    dt_titles = {c.id: c.title for c in list_detection_catalog()}
    pv_titles = {c.id: c.title for c in list_prevention_catalog()}
    for cat in ranked["categories"]:
        cat["detections"] = [{"id": i, "title": dt_titles.get(i, i)} for i in cat["detections"]]
        cat["preventions"] = [{"id": i, "title": pv_titles.get(i, i)} for i in cat["preventions"]]
    # Mention-ranked vendor rows — decoration only, after every ranking field
    # is final (attach never reads or writes category scores/sort).
    attach_vendor_mentions(ranked["categories"], mentions_for_index(get_index(path)))
    return {
        # Same staleness stamp + basis block the EVIDENCE page renders — the
        # TOOLING basis line cites them verbatim.
        "generated_at": ledger["generated_at"],
        "basis": ledger["basis"],
        "enriched_cases": ledger["enriched_cases"],
        "small_n_floor": SMALL_N_FLOOR,
        **ranked,
        "attribution": (
            "Insider Threat Matrix™ is owned by Forscie Limited. "
            "Insider Threat Matrix is a trademark of Forscie Limited."
        ),
    }


def list_articles(
    *,
    limit: int = 50,
    min_score: float = 0.0,
    source_id: str | None = None,
    theme: str | None = None,
    itm_id: str | None = None,
    detection_id: str | None = None,
    prevention_id: str | None = None,
    itm_alignment: str = "insider",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
    topic_match: bool = False,
    group: bool = True,
    path: str | Path | None = None,
) -> ArticleListResponse:
    index = get_index(path)
    return index.list_articles(
        limit=limit,
        min_score=min_score,
        source_id=source_id,
        theme=theme,
        itm_id=itm_id,
        detection_id=detection_id,
        prevention_id=prevention_id,
        itm_alignment=itm_alignment,
        channel=channel,
        use_case=use_case,
        insider_type=insider_type,
        topic_match=topic_match,
        group=group,
    )


def _social_store():
    from apps.aggregator.social_subscriptions import SocialSubscriptionStore

    return SocialSubscriptionStore(get_settings().social_subscriptions_path)


def social_catalog() -> SocialCatalogResponse:
    """Curated suggestions + current subscriptions, with indexed article counts."""
    from apps.aggregator.social_catalog import build_catalog, subscription_to_info

    counts = {sid: count for sid, _name, count in get_index().list_sources(channel="social")}
    subscriptions = [subscription_to_info(s) for s in _social_store().list()]
    subscribed_keys = {(s.platform, s.id) for s in subscriptions if s.subscribed}

    suggestions = build_catalog()
    for info in suggestions:
        info.subscribed = (info.platform, info.id) in subscribed_keys
        info.article_count = counts.get(info.source_id, 0)
    for info in subscriptions:
        info.article_count = counts.get(info.source_id, 0)
    return SocialCatalogResponse(suggestions=suggestions, subscriptions=subscriptions)


def add_social_subscription(
    platform: str,
    handle: str,
    *,
    name: str | None = None,
    use_cases: list[str] | None = None,
) -> SocialSourceInfo:
    from apps.aggregator.social_catalog import build_catalog, subscription_to_info
    from apps.aggregator.social_subscriptions import normalize_handle

    origin = "manual"
    catalog_use_cases: list[str] | None = None
    normalized = normalize_handle(platform, handle)
    for info in build_catalog():
        if info.platform == platform and info.id == normalized:
            origin = "catalog"
            catalog_use_cases = list(info.use_cases)
            break
    entry = _social_store().add(
        platform,
        handle,
        name=name,
        origin=origin,
        use_cases=use_cases or catalog_use_cases,
    )
    return subscription_to_info(entry)


def remove_social_subscription(platform: str, handle: str) -> bool:
    return _social_store().remove(platform, handle)


def list_use_cases() -> list[UseCaseInfo]:
    from shared.taxonomy.use_cases import USE_CASES

    return [UseCaseInfo(id=uc.id, label=uc.label, description=uc.description) for uc in USE_CASES]


def trending(*, window_days: int = 7, limit: int = 8) -> list[dict]:
    """Most-active topics across the indexed feeds (week-over-week deltas)."""
    return get_index().trending(window_days=window_days, limit=limit)


def candidate_catalog() -> CandidateCatalogResponse:
    """The novel-technique candidate view (job-written state; read-only here)."""
    from apps.aggregator.technique_seeds import TechniqueSeedStore

    return TechniqueSeedStore(get_settings().technique_seeds_path).read()


def itm_catalog(
    *,
    source_id: str | None = None,
    channel: str = "all",
) -> ItmCatalogResponse:
    from shared.itm.controls import list_detection_catalog, list_prevention_catalog

    index = load_itm_index()
    article_counts = get_index().technique_article_counts(
        topic_match=False,
        itm_alignment="all",
        min_score=0.0,
        source_id=source_id,
        channel=channel,
    )
    return ItmCatalogResponse(
        itm_version=index.itm_version,
        refreshed_at=index.refreshed_at,
        articles=[ItmArticleSummary(id=a.id, title=a.title, theme=a.theme) for a in index.articles],
        techniques=[
            ItmTechniqueSummary(
                id=t.id,
                title=t.title,
                theme=t.theme,
                article_id=t.article_id,
                parent_id=t.parent_id,
                description=t.description_text or "",
                aliases=list(t.aliases or []),
                article_count=int(article_counts.get(t.id, 0)),
                detections=[ControlRef(id=c.id, title=c.title) for c in t.detections],
                preventions=[ControlRef(id=c.id, title=c.title) for c in t.preventions],
            )
            for t in index.techniques
        ],
        detections=list_detection_catalog(),
        preventions=list_prevention_catalog(),
    )
