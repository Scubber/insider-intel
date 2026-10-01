"""Render a Markdown briefing to simple email HTML (stdlib only).

Companion to scripts/send_digest_email.py for the research-mail workflow:
docs/research/briefings/<slug>.md → an inline-styled HTML body that Gmail and
Outlook render without surprises. Supports what the briefing skeleton uses
(docs/research/routine-prompt.md): #/##/### headings, paragraphs, - and 1.
lists, **bold**, *italic*, `code`, [text](url) and bare https links,
horizontal rules. Everything is HTML-escaped first; link targets are kept
only when they start with http(s).

Usage: markdown_mail.py BRIEFING.md --out body.html [--subject-out subject.txt]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

FONT = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
STYLE = {
    "h1": f"font-size:20px;margin:0 0 8px;{FONT}",
    "h2": f"font-size:16px;margin:22px 0 6px;{FONT}",
    "h3": f"font-size:14px;margin:16px 0 4px;{FONT}",
    "p": f"font-size:14px;line-height:1.5;margin:0 0 10px;color:#111;{FONT}",
    "li": f"font-size:14px;line-height:1.5;margin:0 0 4px;color:#111;{FONT}",
    "hr": "border:0;border-top:1px solid #ddd;margin:16px 0",
    "code": "font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:13px",
}

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?<![\"'>=])\bhttps?://[^\s<)]+")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def inline(text: str) -> str:
    """Escape, then apply inline Markdown."""
    s = html.escape(text, quote=False)
    s = _CODE_RE.sub(lambda m: f'<code style="{STYLE["code"]}">{m.group(1)}</code>', s)
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = _BARE_URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', s)
    s = _BOLD_RE.sub(r"<b>\1</b>", s)
    s = _ITAL_RE.sub(r"<i>\1</i>", s)
    return s


def render(md: str) -> str:
    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            out.append(f'<p style="{STYLE["p"]}">{inline(" ".join(para))}</p>')
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            close_list()
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush_para()
            close_list()
            tag = f"h{len(m.group(1))}"
            out.append(f'<{tag} style="{STYLE[tag]}">{inline(m.group(2))}</{tag}>')
            continue
        if re.match(r"^(-{3,}|\*{3,})$", line.strip()):
            flush_para()
            close_list()
            out.append(f'<hr style="{STYLE["hr"]}">')
            continue
        m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)$", line)
        if m:
            flush_para()
            tag = "ol" if re.match(r"^\s*\d+\.", line) else "ul"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f'<li style="{STYLE["li"]}">{inline(m.group(1))}</li>')
            continue
        if list_tag and line.startswith("  "):
            # continuation of the previous list item
            out[-1] = out[-1][: -len("</li>")] + " " + inline(line.strip()) + "</li>"
            continue
        close_list()
        para.append(line.strip())
    flush_para()
    close_list()
    body = "\n".join(out)
    return (
        '<!doctype html><html><body style="margin:0;padding:16px;background:#fff;color:#111">'
        f'<div style="max-width:720px;margin:0 auto;{FONT}">{body}</div></body></html>'
    )


def title_of(md: str) -> str:
    for line in md.splitlines():
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return "insider-intel research briefing"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("markdown")
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject-out")
    a = ap.parse_args(argv)
    md = Path(a.markdown).read_text(encoding="utf-8")
    Path(a.out).write_text(render(md), encoding="utf-8")
    if a.subject_out:
        Path(a.subject_out).write_text(
            f"insider-intel research · {title_of(md)}\n", encoding="utf-8"
        )
    print(f"rendered {len(md.splitlines())} markdown lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
