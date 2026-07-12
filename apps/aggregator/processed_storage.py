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

    def save(self, articles: list[ProcessedArticle]) -> int:
        if not articles:
            return 0

        new_articles = [a for a in articles if a.link not in self._known_links]
        if not new_articles:
            logger.info("No new processed articles to save (all duplicates)")
            return 0

        with self.path.open("a", encoding="utf-8") as handle:
            for article in new_articles:
                handle.write(article.model_dump_json() + "\n")
                self._known_links.add(article.link)

        logger.info("Saved %d processed article(s) to %s", len(new_articles), self.path)
        return len(new_articles)

    def upsert(self, articles: list[ProcessedArticle]) -> int:
        """Replace existing rows for the same link and append any new links.

        Rewrites the JSONL so each link appears once (keeps the upserted
        version). Used by ``process --force``.
        """
        if not articles:
            return 0

        by_link = {a.link: a for a in self.load_all(dedupe=False)}
        for article in articles:
            by_link[article.link] = article

        ordered = list(by_link.values())
        self.replace_all(ordered)
        return len(articles)

    def replace_all(self, articles: list[ProcessedArticle]) -> None:
        """Atomically rewrite the store to exactly these articles (one per link)."""
        # Last write wins if callers pass duplicates
        by_link: dict[str, ProcessedArticle] = {}
        for article in articles:
            by_link[article.link] = article
        unique = list(by_link.values())

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for article in unique:
                handle.write(article.model_dump_json() + "\n")
        tmp.replace(self.path)
        self._known_links = {a.link for a in unique}
        logger.info("Rewrote %d processed article(s) to %s", len(unique), self.path)

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
        """Rewrite file keeping latest article per link. Returns unique count."""
        unique = self.load_all(dedupe=True)
        self.replace_all(unique)
        return len(unique)

    def forget_links(self, links: list[str]) -> None:
        """Allow subsequent save() to rewrite articles for these links.

        Prefer upsert() / compact() — this only clears the in-memory set.
        """
        for link in links:
            self._known_links.discard(link)
