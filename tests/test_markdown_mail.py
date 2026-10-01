"""Contracts for scripts/markdown_mail.py + the research-mail workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "markdown_mail.py"
_spec = importlib.util.spec_from_file_location("markdown_mail", _SCRIPT)
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

SAMPLE = """# Why so few cases reach a verdict

PUBLISHED 2026-10-03 · AS OF 2026-10-03 CORPUS PULL (9,400 documents)

## Bottom line
Most **matched** cases stop at *complaint*. See [EVIDENCE](https://intel.thederpweb.com/#/evidence)
and https://example.test/ruling for the live view.

## What the record shows
- First finding with `code`
- Second finding
  continues here
1. numbered one
2. numbered two

---

## Limits
<script>alert(1)</script> is text, not code.
"""


def test_render_structure_and_escaping() -> None:
    out = mm.render(SAMPLE)
    assert "<h1" in out and ">Why so few cases reach a verdict</h1>" in out
    assert out.count("<h2") == 3
    assert "<b>matched</b>" in out and "<i>complaint</i>" in out
    assert '<a href="https://intel.thederpweb.com/#/evidence">EVIDENCE</a>' in out
    assert '<a href="https://example.test/ruling">https://example.test/ruling</a>' in out
    assert "<ul>" in out and "<ol>" in out and out.count("<li") == 4
    assert "Second finding continues here" in out
    assert "<hr" in out
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<code" in out
    assert "<style" not in out and "javascript:" not in out


def test_title_and_cli(tmp_path: Path) -> None:
    assert mm.title_of(SAMPLE) == "Why so few cases reach a verdict"
    assert mm.title_of("no heading") == "insider-intel research briefing"
    src = tmp_path / "b.md"
    src.write_text(SAMPLE, encoding="utf-8")
    rc = mm.main(
        [str(src), "--out", str(tmp_path / "b.html"), "--subject-out", str(tmp_path / "s.txt")]
    )
    assert rc == 0
    assert (
        tmp_path / "s.txt"
    ).read_text() == "insider-intel research · Why so few cases reach a verdict\n"
    assert (tmp_path / "b.html").read_text().startswith("<!doctype html>")


def test_research_mail_workflow_contract() -> None:
    wf = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "research-mail.yml"
    ).read_text()
    assert "scripts/markdown_mail.py" in wf and "scripts/send_digest_email.py" in wf
    for secret in ("TRAFFIC_DIGEST_TO", "TRAFFIC_DIGEST_SMTP_USER", "TRAFFIC_DIGEST_SMTP_PASSWORD"):
        assert f"secrets.{secret}" in wf
    assert "docs/research/briefings/*.md" in wf  # path allowlist
    assert "@gmail" not in wf
