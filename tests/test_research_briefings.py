"""Emailed research briefings (docs/research/briefings/) keep the frozen-
numbers contract: a PUBLISHED + AS OF dateline naming the corpus pull, a
Limits section, a Sources section, and no email address anywhere. The
scheduled routine (docs/research/routine-prompt.md) runs this before it
opens its PR; the topic queue must parse and never carry a corpus number.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIEFINGS = ROOT / "docs" / "research" / "briefings"
TOPICS = ROOT / "docs" / "research" / "topics.md"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BANNED = ("delve", "leverage", "robust", "comprehensive", "seamless", "holistic", "utilize")


def _rows():
    text = TOPICS.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith("| Status") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 4:
            rows.append(cells)
    return rows


def test_topic_queue_parses_and_is_authored_only() -> None:
    rows = _rows()
    assert len(rows) >= 10
    slugs = [r[1].strip("`") for r in rows]
    assert len(slugs) == len(set(slugs)), "duplicate slug"
    for r in rows:
        assert re.fullmatch(
            r"queued|drafted \d{4}-\d{2}-\d{2}|sent \d{4}-\d{2}-\d{2}|parked", r[0]
        ), r[0]
        assert re.fullmatch(r"[a-z0-9-]+", r[1].strip("`")), r[1]
        assert r[2].endswith(".") or r[2].endswith("?"), "question is a sentence"
    text = TOPICS.read_text(encoding="utf-8")
    assert not EMAIL_RE.search(text)
    # Seeds may quote outside figures (the operator's post); the queue never
    # states a corpus count. The tell is "cases" next to a bare number.
    assert not re.search(r"\b\d{2,}\s+(verdict-true|adjudicated|alleged)\b", text)


def test_every_briefing_keeps_the_dateline_limits_and_sources() -> None:
    BRIEFINGS.mkdir(exist_ok=True)
    for path in sorted(BRIEFINGS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        head = text[:1200]
        assert "PUBLISHED" in head and "AS OF" in head and "CORPUS" in head.upper(), path.name
        assert re.search(r"(?m)^## Limits\s*$", text), f"{path.name}: Limits section missing"
        assert re.search(r"(?m)^## Sources\s*$", text), f"{path.name}: Sources section missing"
        assert not EMAIL_RE.search(text), f"{path.name}: carries an email address"
        lowered = text.lower()
        for word in BANNED:
            assert not re.search(rf"\b{word}\b", lowered), f"{path.name}: banned word {word!r}"
        assert re.fullmatch(r"[a-z0-9-]+\.md", path.name), path.name
