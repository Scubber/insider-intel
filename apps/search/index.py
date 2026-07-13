"""In-memory search index over processed articles (JSONL-backed MVP)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search.cluster import cluster_hits
from shared.itm.controls import techniques_for_detection, techniques_for_prevention
from shared.itm.index import load_itm_index
from shared.schemas import (
    ArticleListResponse,
    ProcessedArticle,
    SearchHit,
    SearchMode,
    SearchResponse,
)
from shared.schemas.articles import resolve_channel
from shared.utils.embeddings import cosine_similarity, get_default_embedder
from shared.utils.story_key import compute_story_key

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+._-]{1,}", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def _article_channel(article: ProcessedArticle) -> str:
    return resolve_channel(
        article.source_id,
        getattr(article, "channel", None),
    )


def _article_matches_channel(
    article: ProcessedArticle,
    *,
    channel: str = "all",
) -> bool:
    mode = (channel or "all").strip().lower()
    if mode in {"", "all", "*"}:
        return True
    return _article_channel(article) == mode


def _alias_matches(alias: str, lowered: str) -> bool:
    """Substring for multi-word / long phrases; word-boundary for short tokens."""
    if " " in alias or len(alias) >= 8:
        return alias in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered) is not None


def _article_topic_blob(article: ProcessedArticle) -> str:
    parts = [
        article.title or "",
        article.summary or "",
        article.clean_text or "",
    ]
    return " ".join(parts).lower()


def _article_matches_technique_topic(article: ProcessedArticle, itm_id: str) -> bool:
    """True if article text matches technique title/aliases (live topic match)."""
    needle = itm_id.strip().upper()
    if not needle:
        return False
    by_id = {t.id.upper(): t for t in load_itm_index().techniques}
    tech = by_id.get(needle)
    if tech is None:
        return False

    lowered = _article_topic_blob(article)
    if not lowered.strip():
        return False

    phrases: list[str] = []
    title_l = (tech.title or "").strip().lower()
    if title_l:
        phrases.append(title_l)
    for alias in tech.aliases:
        cleaned = (alias or "").strip().lower()
        if cleaned and cleaned not in phrases:
            phrases.append(cleaned)

    phrases.sort(key=len, reverse=True)
    for phrase in phrases:
        if len(phrase) < 3:
            continue
        if _alias_matches(phrase, lowered):
            return True
    return False


def _article_matches_technique_ids(
    article: ProcessedArticle,
    technique_ids: list[str],
    *,
    topic_match: bool = False,
) -> bool:
    """True if article matches any of the technique ids (hit and/or topic)."""
    if not technique_ids:
        return False
    for tech_id in technique_ids:
        if _article_matches_itm(
            article, itm_id=tech_id, topic_match=topic_match
        ):
            return True
    return False


def _article_matches_itm(
    article: ProcessedArticle,
    *,
    theme: str | None = None,
    itm_id: str | None = None,
    detection_id: str | None = None,
    prevention_id: str | None = None,
    topic_match: bool = False,
) -> bool:
    if not theme and not itm_id and not detection_id and not prevention_id:
        return True
    hits = article.entities.itm_hits
    if theme:
        theme_l = theme.strip().lower()
        if not any(h.theme.lower() == theme_l for h in hits):
            return False

    if detection_id:
        tech_ids = techniques_for_detection(detection_id)
        if not _article_matches_technique_ids(
            article, tech_ids, topic_match=topic_match
        ):
            return False
    if prevention_id:
        tech_ids = techniques_for_prevention(prevention_id)
        if not _article_matches_technique_ids(
            article, tech_ids, topic_match=topic_match
        ):
            return False

    if itm_id:
        needle = itm_id.strip().upper()
        if any(
            h.id.upper() == needle or h.id.upper().startswith(needle + ".") for h in hits
        ):
            return True
        if topic_match and _article_matches_technique_topic(article, needle):
            return True
        return False
    return True


def _article_matches_alignment(
    article: ProcessedArticle,
    *,
    itm_alignment: str = "insider",
) -> bool:
    """Default stream is ITM-aligned insider scenarios only."""
    mode = (itm_alignment or "insider").strip().lower()
    if mode in {"", "all", "*"}:
        return True
    alignment = getattr(article, "itm_alignment", None) or "weak"
    return alignment == mode


class ArticleSearchIndex:
    """Keyword + semantic + hybrid search over ProcessedArticle records."""

    def __init__(self, articles: list[ProcessedArticle] | None = None) -> None:
        self._articles: list[ProcessedArticle] = []
        self._by_link: dict[str, ProcessedArticle] = {}
        self._tokens: list[set[str]] = []
        self._embeddings: list[list[float] | None] = []
        self._embedder = get_default_embedder()
        if articles:
            self.build(articles)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> ArticleSearchIndex:
        store = JsonlProcessedStore(path)
        articles = store.load_all()
        logger.info("Loaded %d processed article(s) from %s", len(articles), path)
        return cls(articles)

    def build(self, articles: list[ProcessedArticle]) -> None:
        # Guard against legacy duplicate JSONL rows (same link, multiple lines)
        by_link: dict[str, ProcessedArticle] = {}
        for article in articles:
            by_link[article.link] = article
        unique = list(by_link.values())
        if len(unique) < len(articles):
            logger.info(
                "Deduped search index inputs: %d -> %d unique link(s)",
                len(articles),
                len(unique),
            )

        self._articles = unique
        self._by_link = {article.link: article for article in unique}
        self._tokens = []
        self._embeddings = []
        for article in self._articles:
            itm_blob = " ".join(
                f"{hit.id} {hit.title} {hit.theme}" for hit in article.entities.itm_hits
            )
            blob = " ".join(
                part
                for part in (
                    article.title,
                    article.summary or "",
                    article.clean_text,
                    " ".join(article.entities.cves),
                    " ".join(article.entities.keywords_hit),
                    " ".join(article.entities.operator_terms),
                    itm_blob,
                )
                if part
            )
            self._tokens.append(_tokenize(blob))
            embedding = article.embedding
            if not embedding:
                embedding = self._embedder.embed(article.clean_text or article.title)
            self._embeddings.append(embedding)
        logger.info("Search index built with %d article(s)", len(self._articles))

    @property
    def size(self) -> int:
        return len(self._articles)

    def get_by_link(self, link: str) -> ProcessedArticle | None:
        """Return the indexed article for an exact link, if present."""
        if not link:
            return None
        return self._by_link.get(link)

    def hit_by_link(self, link: str) -> SearchHit | None:
        """Return the SearchHit for an exact link, if indexed."""
        article = self.get_by_link(link)
        if article is None:
            return None
        return self._to_hit(article, 0.0)

    def reload(self, path: str | Path) -> int:
        store = JsonlProcessedStore(path)
        self.build(store.load_all())
        return self.size

    def list_sources(
        self,
        *,
        min_score: float = 0.0,
        theme: str | None = None,
        itm_id: str | None = None,
        itm_alignment: str = "all",
        channel: str = "all",
    ) -> list[tuple[str, str, int]]:
        """Return (source_id, source_name, article_count) matching optional filters."""
        counts: dict[str, tuple[str, int]] = {}
        for article in self._articles:
            if article.relevance_score < min_score:
                continue
            if not _article_matches_itm(article, theme=theme, itm_id=itm_id):
                continue
            if not _article_matches_alignment(article, itm_alignment=itm_alignment):
                continue
            if not _article_matches_channel(article, channel=channel):
                continue
            sid = article.source_id
            name, n = counts.get(sid, (article.source_name, 0))
            counts[sid] = (article.source_name or name, n + 1)
        return [(sid, name, n) for sid, (name, n) in sorted(counts.items())]

    def technique_article_counts(
        self,
        *,
        topic_match: bool = True,
        itm_alignment: str = "all",
        min_score: float = 0.0,
        source_id: str | None = None,
        channel: str = "all",
    ) -> dict[str, int]:
        """Count indexed articles per technique id.

        Default catalog path uses stored ``itm_hits`` only (fast). Topic-text
        matching is reserved for single-technique article queries — scanning
        every technique × every article with topic_match is too slow for /itm.
        """
        tech_ids = [t.id for t in load_itm_index().techniques]
        counts = {tid: 0 for tid in tech_ids}
        known = set(tech_ids)

        for article in self._articles:
            if article.relevance_score < min_score:
                continue
            if source_id and article.source_id != source_id:
                continue
            if not _article_matches_alignment(article, itm_alignment=itm_alignment):
                continue
            if not _article_matches_channel(article, channel=channel):
                continue

            if not topic_match:
                seen: set[str] = set()
                for hit in article.entities.itm_hits:
                    hid = (hit.id or "").strip().upper()
                    if not hid or hid in seen:
                        continue
                    seen.add(hid)
                    if hid in known:
                        counts[hid] += 1
                    parent = hid.split(".", 1)[0]
                    if parent != hid and parent in known and parent not in seen:
                        seen.add(parent)
                        counts[parent] += 1
                continue

            for tech_id in tech_ids:
                if _article_matches_itm(
                    article, itm_id=tech_id, topic_match=True
                ):
                    counts[tech_id] += 1
        return counts

    def list_articles(
        self,
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
        topic_match: bool = False,
        group: bool = True,
    ) -> ArticleListResponse:
        """Return recent articles sorted by published time (newest first).

        When group=True (default), collapse multi-source same-day stories within
        a channel into StoryCluster cards; results = primaries only.
        """
        filtered = [
            a
            for a in self._articles
            if a.relevance_score >= min_score
            and (not source_id or a.source_id == source_id)
            and _article_matches_itm(
                a,
                theme=theme,
                itm_id=itm_id,
                detection_id=detection_id,
                prevention_id=prevention_id,
                topic_match=topic_match,
            )
            and _article_matches_alignment(a, itm_alignment=itm_alignment)
            and _article_matches_channel(a, channel=channel)
        ]
        hits = [self._to_hit(article, article.relevance_score) for article in filtered]

        if group:
            clusters = cluster_hits(hits)[:limit]
            results = [c.primary for c in clusters]
            return ArticleListResponse(
                total_indexed=self.size,
                count=len(results),
                results=results,
                clusters=clusters,
            )

        hits.sort(
            key=lambda h: h.published or datetime.min,
            reverse=True,
        )
        top = hits[:limit]
        return ArticleListResponse(
            total_indexed=self.size,
            count=len(top),
            results=top,
            clusters=[],
        )

    def search(
        self,
        query: str,
        *,
        mode: SearchMode = SearchMode.hybrid,
        limit: int = 10,
        min_score: float = 0.0,
        source_id: str | None = None,
        theme: str | None = None,
        itm_id: str | None = None,
        itm_alignment: str = "insider",
        channel: str = "all",
    ) -> SearchResponse:
        query = (query or "").strip()
        if not query:
            return SearchResponse(
                query=query,
                mode=mode,
                total_indexed=self.size,
                count=0,
                results=[],
            )

        query_tokens = _tokenize(query)
        query_vec = self._embedder.embed(query)
        scored: list[tuple[float, ProcessedArticle]] = []

        for i, article in enumerate(self._articles):
            if source_id and article.source_id != source_id:
                continue
            if not _article_matches_itm(article, theme=theme, itm_id=itm_id):
                continue
            if not _article_matches_alignment(article, itm_alignment=itm_alignment):
                continue
            if not _article_matches_channel(article, channel=channel):
                continue

            kw_score = self._keyword_score(query_tokens, self._tokens[i], article)
            sem_score = 0.0
            emb = self._embeddings[i]
            if emb:
                # Map cosine [-1,1] → [0,1]
                sem_score = max(0.0, (cosine_similarity(query_vec, emb) + 1.0) / 2.0)

            if mode is SearchMode.keyword:
                score = kw_score
            elif mode is SearchMode.semantic:
                score = sem_score
            else:
                # Hybrid: prefer lexical hits, blend semantic
                score = (0.55 * kw_score) + (0.35 * sem_score) + (0.10 * article.relevance_score)

            if score >= min_score:
                scored.append((score, article))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:limit]
        results = [self._to_hit(article, score) for score, article in top]

        return SearchResponse(
            query=query,
            mode=mode,
            total_indexed=self.size,
            count=len(results),
            results=results,
        )

    @staticmethod
    def _keyword_score(
        query_tokens: set[str],
        doc_tokens: set[str],
        article: ProcessedArticle,
    ) -> float:
        if not query_tokens:
            return 0.0

        overlap = query_tokens & doc_tokens
        if not overlap:
            # substring fallback for short queries (e.g. partial CVE)
            q = " ".join(sorted(query_tokens))
            hay = f"{article.title} {article.clean_text}".lower()
            if q and q in hay:
                return 0.4
            return 0.0

        coverage = len(overlap) / len(query_tokens)
        title_tokens = _tokenize(article.title)
        title_boost = 0.15 if query_tokens & title_tokens else 0.0
        cve_boost = 0.2 if any(t.upper().startswith("CVE") for t in overlap) else 0.0
        itm_ids = {h.id.upper() for h in article.entities.itm_hits}
        itm_boost = 0.1 if any(t.upper() in itm_ids for t in overlap) else 0.0
        return min(1.0, coverage + title_boost + cve_boost + itm_boost)

    @staticmethod
    def _to_hit(article: ProcessedArticle, score: float) -> SearchHit:
        story_key = (getattr(article, "story_key", None) or "").strip()
        if not story_key:
            story_key = compute_story_key(
                article.title,
                article.published,
                fallback=getattr(article, "processed_at", None),
            )
        return SearchHit(
            title=article.title,
            link=article.link,
            source_id=article.source_id,
            source_name=article.source_name,
            channel=_article_channel(article),
            published=article.published,
            summary=article.summary,
            relevance_score=article.relevance_score,
            score=round(score, 4),
            cves=list(article.entities.cves),
            domains=list(article.entities.domains),
            keywords_hit=list(article.entities.keywords_hit),
            operator_terms=list(article.entities.operator_terms),
            itm_hits=list(article.entities.itm_hits),
            related_detections=list(
                getattr(article.entities, "related_detections", None) or []
            ),
            related_preventions=list(
                getattr(article.entities, "related_preventions", None) or []
            ),
            itm_alignment=getattr(article, "itm_alignment", None) or "weak",
            story_key=story_key,
        )
