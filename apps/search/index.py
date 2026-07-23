"""In-memory search index over processed articles (JSONL-backed MVP)."""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
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
# Bare taxonomy ids ("me024", "if002.3") read as noise in trending terms.
_ITM_ID_TERM_RE = re.compile(r"[a-z]{2}\d{3}(\.\d+)?")


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def _as_utc(dt: datetime) -> datetime:
    """Naive timestamps in the corpus are UTC by convention."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


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


def _passes_min_score(article: ProcessedArticle, min_score: float) -> bool:
    """Relevance floor, except curated publications: long reference docs dilute
    keyword density and score low by nature, and they already bypass the
    process-time gate — hiding them behind a stream floor would contradict that.
    """
    if article.relevance_score >= min_score:
        return True
    return _article_channel(article) == "publications"


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
        if _article_matches_itm(article, itm_id=tech_id, topic_match=topic_match):
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
        if not _article_matches_technique_ids(article, tech_ids, topic_match=topic_match):
            return False
    if prevention_id:
        tech_ids = techniques_for_prevention(prevention_id)
        if not _article_matches_technique_ids(article, tech_ids, topic_match=topic_match):
            return False

    if itm_id:
        needle = itm_id.strip().upper()
        if any(h.id.upper() == needle or h.id.upper().startswith(needle + ".") for h in hits):
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


def _article_matches_use_case(
    article: ProcessedArticle,
    *,
    use_case: str | None = None,
) -> bool:
    mode = (use_case or "all").strip().lower()
    if mode in {"", "all", "*"}:
        return True
    return mode in (getattr(article, "use_cases", None) or [])


def _article_matches_insider_type(
    article: ProcessedArticle,
    *,
    insider_type: str = "all",
) -> bool:
    """'none' selects unclassified articles; 'all' passes everything."""
    mode = (insider_type or "all").strip().lower()
    if mode in {"", "all", "*"}:
        return True
    value = getattr(article, "insider_type", None)
    if mode in {"none", "unclassified"}:
        return value is None
    return value == mode


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

    @property
    def last_processed_at(self) -> datetime | None:
        """Newest processed_at in the corpus — 'freshness' for /health."""
        stamps = [a.processed_at for a in self._articles if a.processed_at]
        return max(stamps) if stamps else None

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
        use_case: str | None = None,
        insider_type: str = "all",
    ) -> list[tuple[str, str, int]]:
        """Return (source_id, source_name, article_count) matching optional filters."""
        counts: dict[str, tuple[str, int]] = {}
        for article in self._articles:
            if not _passes_min_score(article, min_score):
                continue
            if not _article_matches_itm(article, theme=theme, itm_id=itm_id):
                continue
            if not _article_matches_alignment(article, itm_alignment=itm_alignment):
                continue
            if not _article_matches_channel(article, channel=channel):
                continue
            if not _article_matches_use_case(article, use_case=use_case):
                continue
            if not _article_matches_insider_type(article, insider_type=insider_type):
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
        use_case: str | None = None,
        insider_type: str = "all",
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
            if not _passes_min_score(article, min_score):
                continue
            if source_id and article.source_id != source_id:
                continue
            if not _article_matches_alignment(article, itm_alignment=itm_alignment):
                continue
            if not _article_matches_channel(article, channel=channel):
                continue
            if not _article_matches_use_case(article, use_case=use_case):
                continue
            if not _article_matches_insider_type(article, insider_type=insider_type):
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
                if _article_matches_itm(article, itm_id=tech_id, topic_match=True):
                    counts[tech_id] += 1
        return counts

    def trending(
        self,
        *,
        window_days: int = 7,
        limit: int = 8,
        min_term_count: int = 3,
    ) -> list[dict]:
        """Most-common topics across the indexed feeds, ranked by volume.

        Pure counting over the in-memory corpus — no LLM, no extra I/O. Topics
        are classified use cases, ITM parent techniques, and hot matched terms;
        `count` is the number of unique stories (story_key) mapped to each topic
        across the WHOLE corpus, and items are ranked by that volume (most-common
        first). Each item also carries a secondary trend arrow (delta_pct /
        direction) comparing the recent window vs the prior window, anchored on
        the corpus's newest processed_at (not wall clock) for stable, testable
        results. `window_days` sizes only the trend arrow, not the ranking.
        """
        anchor = self.last_processed_at
        if anchor is None:
            return []
        anchor = _as_utc(anchor)
        window = timedelta(days=max(1, window_days))
        recent_start = anchor - window
        prior_start = anchor - (2 * window)

        tech_titles = {t.id.upper(): t.title for t in load_itm_index().techniques}
        from shared.taxonomy.use_cases import USE_CASES

        uc_labels = {uc.id: uc.label for uc in USE_CASES}
        # Terms that merely restate a use-case/technique label add no signal.
        redundant_terms = {label.lower() for label in uc_labels.values()} | {
            title.lower() for title in tech_titles.values()
        }

        topics: dict[tuple[str, str], dict] = {}

        def bucket_for(article: ProcessedArticle) -> str | None:
            stamp = article.published or article.processed_at
            if stamp is None:
                return None
            stamp = _as_utc(stamp)
            if stamp >= recent_start:
                return "recent"
            if stamp >= prior_start:
                return "prior"
            return None

        def touch(kind: str, key: str, label: str, bucket: str | None, story: str, channel: str):
            topic = topics.setdefault(
                (kind, key),
                {
                    "kind": kind,
                    "key": key,
                    "label": label,
                    "total": set(),
                    "recent": set(),
                    "prior": set(),
                    "channels": Counter(),
                },
            )
            topic["total"].add(story)
            topic["channels"][channel] += 1
            if bucket in ("recent", "prior"):
                topic[bucket].add(story)

        for article in self._articles:
            bucket = bucket_for(article)
            story = getattr(article, "story_key", None) or article.link
            channel = _article_channel(article)

            for uc_id in getattr(article, "use_cases", None) or []:
                touch("use_case", uc_id, uc_labels.get(uc_id, uc_id), bucket, story, channel)

            seen_parents: set[str] = set()
            terms: set[str] = set()
            for hit in article.entities.itm_hits:
                pid = (hit.id or "").strip().upper().split(".", 1)[0]
                if pid and pid not in seen_parents:
                    seen_parents.add(pid)
                    label = tech_titles.get(pid) or hit.title or pid
                    touch("technique", pid, label, bucket, story, channel)
                for alias in hit.matched_aliases:
                    terms.add(str(alias).strip().lower())
            for kw in article.entities.keywords_hit:
                terms.add(str(kw).strip().lower())
            for term in terms:
                if len(term) < 3 or term in redundant_terms or _ITM_ID_TERM_RE.fullmatch(term):
                    continue
                touch("term", term, term, bucket, story, channel)

        items: list[dict] = []
        for topic in topics.values():
            count = len(topic["total"])
            recent = len(topic["recent"])
            prev = len(topic["prior"])
            floor = min_term_count if topic["kind"] == "term" else 2
            if count < floor:
                continue
            # Secondary trend arrow: recent window vs prior window.
            if prev > 0:
                delta_pct = round((recent - prev) / prev * 100, 1)
                direction = "up" if delta_pct > 0 else "down" if delta_pct < 0 else "flat"
            elif recent > 0:
                delta_pct = None
                direction = "new"
            else:
                delta_pct = None
                direction = "flat"
            channel = topic["channels"].most_common(1)[0][0] if topic["channels"] else "news"
            items.append(
                {
                    "kind": topic["kind"],
                    "key": topic["key"],
                    "label": topic["label"],
                    "channel": channel,
                    "count": count,
                    "prev_count": prev,
                    "delta_pct": delta_pct,
                    "direction": direction,
                }
            )

        # Most-common first: rank by total volume across the corpus, then label.
        # The recent-vs-prior delta stays on each item as a secondary trend arrow.
        items.sort(key=lambda item: (-item["count"], item["label"]))
        return items[: max(1, limit)]

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
        use_case: str | None = None,
        insider_type: str = "all",
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
            if _passes_min_score(a, min_score)
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
            and _article_matches_use_case(a, use_case=use_case)
            and _article_matches_insider_type(a, insider_type=insider_type)
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
        use_case: str | None = None,
        insider_type: str = "all",
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
            if not _article_matches_use_case(article, use_case=use_case):
                continue
            if not _article_matches_insider_type(article, insider_type=insider_type):
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
            related_detections=list(getattr(article.entities, "related_detections", None) or []),
            related_preventions=list(getattr(article.entities, "related_preventions", None) or []),
            itm_alignment=getattr(article, "itm_alignment", None) or "weak",
            story_key=story_key,
            use_cases=list(getattr(article, "use_cases", None) or []),
            insider_type=getattr(article, "insider_type", None),
            ai_summary=getattr(article, "ai_summary", None),
            case_record=getattr(article, "case_record", None),
            forensics=getattr(article, "forensics", None),
        )
