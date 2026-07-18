"""Simple article storage for the MVP.

JSONL file storage is used until PostgreSQL is wired up. The ArticleStore
protocol keeps the pipeline swappable for a database backend later.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Protocol

from shared.schemas import RawArticle

logger = logging.getLogger(__name__)


def article_fingerprint(title: str | None, summary: str | None) -> str:
    """Content fingerprint used to detect updated articles (link-stable)."""
    payload = f"{title or ''}\x1f{summary or ''}".encode()
    return hashlib.sha1(payload).hexdigest()


class ArticleStore(Protocol):
    """Interface for persisting raw articles."""

    def save(self, articles: list[RawArticle]) -> int:
        """Persist articles. Returns number of newly written records."""
        ...


class JsonlArticleStore:
    """Append-mostly JSON Lines store with link-based de-duplication.

    Each line is one RawArticle as JSON. ``save`` skips links already in the
    file so re-runs are safe; ``refresh`` additionally rewrites rows whose
    content changed (updated dockets) or that gained a full-text body.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # link -> (fingerprint, has_content); derived from the file at load.
        self._index = self._load_index()

    def _load_index(self) -> dict[str, tuple[str, bool]]:
        if not self.path.exists():
            return {}

        index: dict[str, tuple[str, bool]] = {}
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
                            index[link] = (
                                article_fingerprint(payload.get("title"), payload.get("summary")),
                                bool(payload.get("content")),
                            )
                    except json.JSONDecodeError:
                        logger.warning("Skipping corrupt JSONL line %d in %s", line_no, self.path)
        except OSError as exc:
            logger.error("Failed reading store %s: %s", self.path, exc)
            raise
        return index

    def has_link(self, link: str) -> bool:
        return link in self._index

    def _index_entry(self, article: RawArticle) -> tuple[str, bool]:
        return (
            article_fingerprint(article.title, article.summary),
            bool(article.content),
        )

    def save(self, articles: list[RawArticle]) -> int:
        if not articles:
            return 0

        new_articles = [a for a in articles if a.link not in self._index]
        if not new_articles:
            logger.info("No new articles to save (all duplicates)")
            return 0

        try:
            with self.path.open("a", encoding="utf-8") as handle:
                for article in new_articles:
                    handle.write(article.model_dump_json() + "\n")
                    self._index[article.link] = self._index_entry(article)
        except OSError as exc:
            logger.error("Failed writing store %s: %s", self.path, exc)
            raise

        logger.info("Saved %d new article(s) to %s", len(new_articles), self.path)
        return len(new_articles)

    def refresh(self, articles: list[RawArticle], *, force: bool = False) -> tuple[int, int]:
        """Save new articles and rewrite ones whose content changed.

        A stored row is replaced when its title/summary fingerprint differs
        or when the incoming article carries a full-text ``content`` body the
        stored row lacks (one-time backfill). With ``force=True`` every known
        link in ``articles`` is rewritten unconditionally — used by content
        backfills that replace an existing (non-empty) ``content`` body, which
        the fingerprint gate cannot see. Returns (new, updated).
        """
        if not articles:
            return (0, 0)

        updates: list[RawArticle] = []
        for article in articles:
            existing = self._index.get(article.link)
            if existing is None:
                continue
            if force:
                updates.append(article)
                continue
            fingerprint, has_content = existing
            new_fingerprint, new_has_content = self._index_entry(article)
            if fingerprint != new_fingerprint or (new_has_content and not has_content):
                updates.append(article)

        saved = self.save([a for a in articles if a.link not in self._index])
        if not updates:
            return (saved, 0)

        by_link = {a.link: a for a in updates}
        rows: list[RawArticle] = []
        seen: set[str] = set()
        for stored in self.load_all():
            if stored.link in seen:
                continue
            seen.add(stored.link)
            rows.append(by_link.get(stored.link, stored))

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(row.model_dump_json() + "\n")
            tmp.replace(self.path)
        except OSError as exc:
            logger.error("Failed rewriting store %s: %s", self.path, exc)
            raise
        for article in updates:
            self._index[article.link] = self._index_entry(article)

        logger.info("Refreshed store %s: %d new, %d updated", self.path, saved, len(updates))
        return (saved, len(updates))

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
