"""Download upstream ITM JSON and write a slim local index.

Usage:
  python -m shared.itm.refresh
  python -m apps.aggregator refresh_itm
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from shared.itm.aliases import CURATED_ALIASES
from shared.itm.index import DEFAULT_INDEX_PATH, clear_itm_cache

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/forscie/insider-threat-matrix/main/"
    "insider-threat-matrix.json"
)

_DESC_MAX = 320
_WS_RE = re.compile(r"\s+")


def _clean_text(value: str | None, *, max_len: int | None = None) -> str:
    text = _WS_RE.sub(" ", (value or "").strip())
    if max_len is not None and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _aliases_for(tech_id: str, title: str) -> list[str]:
    aliases: list[str] = []
    title_l = title.strip().lower()
    if title_l:
        aliases.append(title_l)
    for extra in CURATED_ALIASES.get(tech_id, ()):
        cleaned = extra.strip().lower()
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
    return aliases


def _control_refs(items: list[Any] | None) -> list[dict[str, str]]:
    """Slim DT/PV entries to id + title only (deduped, ordered)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        ctitle = str(item.get("title") or "").strip()
        if not cid or not ctitle or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "title": ctitle})
    return out


def _technique_row(
    *,
    tech_id: str,
    title: str,
    article_id: str,
    theme: str,
    parent_id: str | None,
    description_text: str | None,
    detections: list[Any] | None = None,
    preventions: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": tech_id,
        "title": title.strip(),
        "article_id": article_id,
        "theme": theme,
        "parent_id": parent_id,
        "description_text": _clean_text(description_text, max_len=_DESC_MAX),
        "aliases": _aliases_for(tech_id, title),
        "detections": _control_refs(detections),
        "preventions": _control_refs(preventions),
    }


def slim_matrix(payload: dict[str, Any], *, source_url: str) -> dict[str, Any]:
    """Convert full upstream ITM JSON into the slim index schema."""
    articles_out: list[dict[str, Any]] = []
    techniques_out: list[dict[str, Any]] = []

    for article in payload.get("articles") or []:
        article_id = str(article.get("id") or "").strip()
        title = str(article.get("title") or "").strip()
        theme = str(article.get("theme") or title.lower()).strip().lower()
        if not article_id or not title:
            continue
        articles_out.append({"id": article_id, "title": title, "theme": theme})

        for section in article.get("sections") or []:
            section_id = str(section.get("id") or "").strip()
            section_title = str(section.get("title") or "").strip()
            if not section_id or not section_title:
                continue
            techniques_out.append(
                _technique_row(
                    tech_id=section_id,
                    title=section_title,
                    article_id=article_id,
                    theme=theme,
                    parent_id=None,
                    description_text=section.get("description_text"),
                    detections=section.get("detections"),
                    preventions=section.get("preventions"),
                )
            )
            for sub in section.get("subsections") or []:
                sub_id = str(sub.get("id") or "").strip()
                sub_title = str(sub.get("title") or "").strip()
                if not sub_id or not sub_title:
                    continue
                techniques_out.append(
                    _technique_row(
                        tech_id=sub_id,
                        title=sub_title,
                        article_id=article_id,
                        theme=theme,
                        parent_id=section_id,
                        description_text=sub.get("description_text"),
                        detections=sub.get("detections"),
                        preventions=sub.get("preventions"),
                    )
                )

    return {
        "itm_version": payload.get("itm_version"),
        "mitre_version": payload.get("mitre_version"),
        "refreshed_at": datetime.now(UTC).isoformat(),
        "source_url": source_url,
        "articles": articles_out,
        "techniques": techniques_out,
    }


def download_matrix(url: str) -> dict[str, Any]:
    logger.info("Downloading ITM JSON from %s", url)
    with urlopen(url, timeout=120) as response:  # noqa: S310 — pinned upstream URL
        return json.loads(response.read().decode("utf-8"))


def refresh_itm_index(
    *,
    source_url: str = DEFAULT_SOURCE_URL,
    output_path: str | Path | None = None,
    source_path: str | Path | None = None,
) -> Path:
    """Build and write the slim index. Returns the output path."""
    out = Path(output_path) if output_path else DEFAULT_INDEX_PATH
    if source_path:
        with Path(source_path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        url = str(source_path)
    else:
        payload = download_matrix(source_url)
        url = source_url

    slim = slim_matrix(payload, source_url=url)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(slim, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    clear_itm_cache()
    logger.info(
        "Wrote slim ITM index (%d techniques, %d articles) → %s",
        len(slim["techniques"]),
        len(slim["articles"]),
        out,
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh slim Insider Threat Matrix™ index.")
    parser.add_argument("--url", default=DEFAULT_SOURCE_URL, help="Upstream ITM JSON URL")
    parser.add_argument(
        "--from-file",
        dest="source_path",
        default=None,
        help="Build from a local full ITM JSON file instead of downloading",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_INDEX_PATH),
        help="Output path for slim itm_index.json",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    path = refresh_itm_index(
        source_url=args.url,
        output_path=args.output,
        source_path=args.source_path,
    )
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
