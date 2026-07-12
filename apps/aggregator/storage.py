"""Simple article storage for the MVP.

JSONL file storage is used until PostgreSQL is wired up. The ArticleStore
protocol keeps the pipeline swappable for a database backend later.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from shared.schemas import RawArticle

logger = logging.getLogger(__name__)


class ArticleStore(Protocol):
    """Interface for persisting raw articles."""

    def save(self, articles: list[RawArticle]) -> int:
        """Persist articles. Returns number of newly written records."""
        ...


class JsonlArticleStore:
    """Append-only JSON Lines store with link-based de-duplication.

    Each line is one RawArticle as JSON. Existing links in the file are
    skipped on subsequent saves so re-runs are safe.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._known_links = self._load_known_links()

    def _load_known_links(self) -> set[str]:
        if not self.path.exists():
            return set()

        known: set[str] = set()
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                        link = payload.get("link")
                        if link:
                            known.add(link)
                    except json.JSONDecodeError:
                        logger.warning("Skipping corrupt JSONL line %d in %s", line_no, self.path)
        except OSError as exc:
            logger.error("Failed reading store %s: %s", self.path, exc)
            raise
        return known

    def save(self, articles: list[RawArticle]) -> int:
        if not articles:
            return 0

        new_articles = [a for a in articles if a.link not in self._known_links]
        if not new_articles:
            logger.info("No new articles to save (all duplicates)")
            return 0

        try:
            with self.path.open("a", encoding="utf-8") as handle:
                for article in new_articles:
                    handle.write(article.model_dump_json() + "\n")
                    self._known_links.add(article.link)
        except OSError as exc:
            logger.error("Failed writing store %s: %s", self.path, exc)
            raise

        logger.info("Saved %d new article(s) to %s", len(new_articles), self.path)
        return len(new_articles)

    def load_all(self) -> list[RawArticle]:
        """Load all stored articles (best-effort; skips corrupt lines)."""
        if not self.path.exists():
            return []

        articles: list[RawArticle] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    articles.append(RawArticle.model_validate_json(line))
                except Exception:
                    logger.warning("Skipping corrupt JSONL line %d in %s", line_no, self.path)
        return articles
