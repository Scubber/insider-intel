"""JSONL storage for processed articles (link de-dupe)."""

from __future__ import annotations

import logging
from pathlib import Path

from shared.schemas import ProcessedArticle

logger = logging.getLogger(__name__)


class JsonlProcessedStore:
    """Append-only store for ProcessedArticle records with link de-dupe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._known_links = self._load_known_links()

    def _load_known_links(self) -> set[str]:
        return {a.link for a in self.load_all()}

    def _ensure_trailing_newline(self) -> None:
        """Heal a torn final line (kill mid-append) before appending after it.

        Without this, the first append after a crash would concatenate onto the
        torn fragment and corrupt an otherwise-good row.
        """
        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return
            with self.path.open("rb") as handle:
                handle.seek(-1, 2)
                last = handle.read(1)
            if last != b"\n":
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write("\n")
        except OSError:
            logger.warning("Could not verify trailing newline for %s", self.path)

    def save(self, articles: list[ProcessedArticle]) -> int:
        if not articles:
            return 0

        new_articles = [a for a in articles if a.link not in self._known_links]
        if not new_articles:
            logger.info("No new processed articles to save (all duplicates)")
            return 0

        self._ensure_trailing_newline()
        with self.path.open("a", encoding="utf-8") as handle:
            for article in new_articles:
                handle.write(article.model_dump_json() + "\n")
                self._known_links.add(article.link)

        logger.info("Saved %d processed article(s) to %s", len(new_articles), self.path)
        return len(new_articles)

    def upsert(self, articles: list[ProcessedArticle]) -> int:
        """Append updated rows; the reader's last-line-wins dedupe makes it an upsert.

        Deliberately NOT a rewrite. The 2026-08-16 staging proof run lost 10 of
        14 enrichments because a later stage's whole-file rewrite carried stale
        copies of rows an earlier stage had already updated — with append-only
        writes a stage can only ever affect the links it explicitly writes,
        never erase another stage's work. The file grows by the appended rows
        until ``compact()`` folds duplicates at cycle end; ``load_all()``
        (last line wins) is unaffected either way. A kill mid-append leaves at
        worst one torn final line, which ``load_all`` already skips.
        """
        if not articles:
            return 0

        enriched = sum(1 for a in articles if getattr(a, "forensics", None) is not None)
        self._ensure_trailing_newline()
        with self.path.open("a", encoding="utf-8") as handle:
            for article in articles:
                handle.write(article.model_dump_json() + "\n")
                self._known_links.add(article.link)
        logger.info(
            "Upserted %d processed article(s) (%d enriched) to %s (append; compacted at cycle end)",
            len(articles),
            enriched,
            self.path,
        )
        return len(articles)

    def replace_all(self, articles: list[ProcessedArticle]) -> None:
        """Atomically rewrite the store to exactly these articles (one per link).

        DANGER: a caller passing rows loaded before another writer's update
        will silently revert that update (the 2026-08-16 clobber). Mid-cycle
        writers must use ``upsert()``; this belongs to ``compact()`` and
        explicit recovery paths only.
        """
        # Last write wins if callers pass duplicates
        by_link: dict[str, ProcessedArticle] = {}
        for article in articles:
            by_link[article.link] = article
        unique = list(by_link.values())

        enriched = sum(1 for a in unique if getattr(a, "forensics", None) is not None)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for article in unique:
                handle.write(article.model_dump_json() + "\n")
        tmp.replace(self.path)
        self._known_links = {a.link for a in unique}
        logger.info(
            "Rewrote %d processed article(s) (%d enriched) to %s",
            len(unique),
            enriched,
            self.path,
        )

    def load_all(self, *, dedupe: bool = True) -> list[ProcessedArticle]:
        """Load articles. With dedupe=True (default), keep the latest row per link."""
        if not self.path.exists():
            return []

        articles: list[ProcessedArticle] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    articles.append(ProcessedArticle.model_validate_json(line))
                except Exception:
                    logger.warning("Skipping corrupt JSONL line %d in %s", line_no, self.path)

        if not dedupe:
            return articles

        by_link: dict[str, ProcessedArticle] = {}
        for article in articles:
            by_link[article.link] = article  # later lines win
        return list(by_link.values())

    def has_link(self, link: str) -> bool:
        return link in self._known_links

    def compact(self) -> int:
        """Rewrite file keeping latest article per link. Returns unique count.

        The one sanctioned full rewrite: run once at cycle end to fold the
        rows ``upsert()`` appended. Safe because it loads at call time and
        keeps last-line-wins order — it can't lose any writer's update.
        """
        unique = self.load_all(dedupe=True)
        self.replace_all(unique)
        return len(unique)

    def forget_links(self, links: list[str]) -> None:
        """Allow subsequent save() to rewrite articles for these links.

        Prefer upsert() / compact() — this only clears the in-memory set.
        """
        for link in links:
            self._known_links.discard(link)
