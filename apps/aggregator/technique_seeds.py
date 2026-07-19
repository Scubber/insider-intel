"""Corpus-level novel-technique candidate store + rebuild aggregation.

The per-case discovery pass (``shared/agents/discover.py``) tags novel behaviors
on each ``ProcessedArticle.discovery``. This module clusters those across the
whole corpus into ``NovelCandidate`` records with an auto-computed lifecycle
(seed → corroborated → eligible) and persists the materialized view to
``data/state/technique_seeds.json``. It is recomputed from scratch every refresh
(idempotent, deterministic, no LLM), and only the refresh job writes it — the
API reads it, respecting the read-only-except-``config/`` bucket invariant.

Promotion is auto-computed and conservative: eligible candidates are *flagged
for human review*, never minted into a permanent technique id. Corroboration is
counted by distinct incident (``story_key``), not distinct URLs — multiple
outlets covering one case are not independent evidence of a technique.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from shared.schemas.discovery import (
    CandidateCatalogResponse,
    NovelCandidate,
    SupportingCase,
)
from shared.utils.embeddings import cosine_similarity, get_default_embedder

logger = logging.getLogger(__name__)

DEFAULT_SEEDS_PATH = "data/state/technique_seeds.json"

# Two novel behaviors cluster together above this cosine (hashing embedding).
CLUSTER_THRESHOLD = 0.72
# A candidate is "clearly distinct" from the ITM only when its max cosine to
# every catalog technique description is below this.
ITM_DISTINCTNESS_THRESHOLD = 0.60

_STRENGTH_RANK = {"weak": 0, "moderate": 1, "strong": 2}
_STATUS_ORDER = {"eligible": 0, "corroborated": 1, "seed": 2}


class TechniqueSeedStore:
    """Atomic JSON store for the novel-candidate view (tmp + replace)."""

    def __init__(self, path: str | Path = DEFAULT_SEEDS_PATH) -> None:
        self.path = Path(path)

    def read(self) -> CandidateCatalogResponse:
        if not self.path.exists():
            return CandidateCatalogResponse()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return CandidateCatalogResponse.model_validate(payload)
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable technique-seeds store %s: %s", self.path, exc)
            return CandidateCatalogResponse()

    def write(self, response: CandidateCatalogResponse) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            response.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)


def _normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _normalize_vec(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class _Record:
    """One novel-behavior observation feeding the clustering."""

    __slots__ = (
        "text",
        "label",
        "link",
        "title",
        "story_key",
        "domain",
        "evidence_strength",
        "claim_status",
        "published",
        "vec",
    )

    def __init__(self, **kw: object) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _collect_records(processed_store) -> list[_Record]:
    embedder = get_default_embedder()
    records: list[_Record] = []
    for article in processed_store.load_all():
        discovery = getattr(article, "discovery", None)
        forensics = getattr(article, "forensics", None)
        if discovery is None or forensics is None:
            continue
        methods = list(forensics.methods)
        for assessment in discovery.novel_assessments():
            novel = assessment.novel
            if novel is None:
                continue
            idx = assessment.method_index
            claim = methods[idx].claim_status if 0 <= idx < len(methods) else "unclear"
            text = f"{novel.label} {novel.portable_behavior}".strip()
            records.append(
                _Record(
                    text=text,
                    label=novel.label or novel.portable_behavior[:80],
                    link=article.link,
                    title=article.title,
                    story_key=article.story_key or article.link,
                    domain=urlparse(article.link).netloc,
                    evidence_strength=assessment.evidence_strength,
                    claim_status=claim,
                    published=article.published or article.processed_at,
                    vec=embedder.embed(_normalize_text(text)),
                )
            )
    # Deterministic order so clustering output is stable across refreshes.
    records.sort(key=lambda r: (r.link, r.text))
    return records


def _cluster(records: list[_Record]) -> list[list[_Record]]:
    clusters: list[list[_Record]] = []
    centroids: list[list[float]] = []
    for rec in records:
        best_i, best_sim = -1, CLUSTER_THRESHOLD
        for i, centroid in enumerate(centroids):
            sim = cosine_similarity(rec.vec, centroid)
            if sim >= best_sim:
                best_i, best_sim = i, sim
        if best_i == -1:
            clusters.append([rec])
            centroids.append(list(rec.vec))
        else:
            clusters[best_i].append(rec)
            summed = [a + b for a, b in zip(centroids[best_i], rec.vec, strict=True)]
            centroids[best_i] = _normalize_vec(summed)
    return clusters


def _representative(cluster: list[_Record]) -> _Record:
    """Highest-evidence, then longest text — deterministic cluster spokesperson."""
    return max(
        cluster,
        key=lambda r: (_STRENGTH_RANK.get(r.evidence_strength, 0), len(r.text or ""), r.link),
    )


def _max_itm_similarity(text: str) -> tuple[float, str | None]:
    from shared.agents.summarize import _technique_vectors

    vec = get_default_embedder().embed(_normalize_text(text))
    if not any(vec):
        return 0.0, None
    best_sim, best_id = 0.0, None
    for tech_id, tvec in _technique_vectors():
        sim = cosine_similarity(vec, tvec)
        if sim > best_sim:
            best_sim, best_id = sim, tech_id
    return best_sim, best_id


def _candidate_from_cluster(cluster: list[_Record]) -> NovelCandidate:
    rep = _representative(cluster)
    story_keys = {r.story_key for r in cluster}
    domains = {r.domain for r in cluster if r.domain}
    corroboration = len(story_keys)
    strength = max(cluster, key=lambda r: _STRENGTH_RANK.get(r.evidence_strength, 0))
    evidence_strength = strength.evidence_strength
    max_sim, nearest = _max_itm_similarity(rep.text)

    is_corroborated = corroboration >= 2
    is_distinct = max_sim < ITM_DISTINCTNESS_THRESHOLD
    if is_corroborated and is_distinct:
        status = "eligible"
    elif is_corroborated:
        status = "corroborated"
    else:
        status = "seed"
    # Evidence gate: never promote off allegation-only / inference-only evidence.
    if evidence_strength == "weak":
        status = "seed"

    supporting = [
        SupportingCase(
            link=r.link,
            title=r.title or "",
            source_domain=r.domain or "",
            story_key=r.story_key or "",
            evidence_strength=r.evidence_strength or "weak",
            claim_status=r.claim_status or "unclear",
        )
        for r in cluster
    ]
    published = [r.published for r in cluster if r.published]
    candidate_id = "NOVEL-" + hashlib.sha1(_normalize_text(rep.label).encode()).hexdigest()[:10]
    return NovelCandidate(
        id=candidate_id,
        label=rep.label,
        portable_behavior=rep.text,
        status=status,  # type: ignore[arg-type]
        flagged_for_review=(status == "eligible"),
        corroboration_count=corroboration,
        distinct_domains=len(domains),
        max_itm_similarity=round(max_sim, 4),
        nearest_itm_id=nearest,
        evidence_strength=evidence_strength,  # type: ignore[arg-type]
        supporting_cases=supporting,
        first_seen=min(published) if published else None,
        last_seen=max(published) if published else None,
    )


def rebuild_technique_seeds(
    processed_store,
    *,
    store: TechniqueSeedStore | None = None,
    generated_at: datetime | None = None,
) -> int:
    """Recompute the novel-candidate view from the whole corpus and persist it.

    Pure aggregation over stored discovery records — no LLM. Idempotent and
    deterministic: same corpus → byte-identical output. Returns the candidate
    count. ``generated_at`` is injected (the pipeline stamps it) so the function
    stays deterministic for tests.
    """
    store = store or TechniqueSeedStore()
    records = _collect_records(processed_store)
    candidates = [_candidate_from_cluster(c) for c in _cluster(records)]
    candidates.sort(
        key=lambda c: (_STATUS_ORDER.get(c.status, 3), -c.corroboration_count, c.id)
    )
    counts: dict[str, int] = {}
    for cand in candidates:
        counts[cand.status] = counts.get(cand.status, 0) + 1
    response = CandidateCatalogResponse(
        generated_at=generated_at,
        candidate_count=len(candidates),
        counts_by_status=counts,
        candidates=candidates,
    )
    store.write(response)
    logger.info(
        "Rebuilt technique seeds: %d candidate(s) %s",
        len(candidates),
        counts,
    )
    return len(candidates)
