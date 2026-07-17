"""Headless UX smoke test for the insider-intel web UI.

Boots web/ in demo mode with a throwaway http.server and drives the core
investigator journeys in a real browser, asserting each works. Run this after
any change to web/ — it is the standing "does the UI still work" check.

Usage:
  python scripts/ui_smoke.py            # serve web/ on an ephemeral port, run
  python scripts/ui_smoke.py --url URL  # test an already-running instance
  python scripts/ui_smoke.py --headed   # watch it run

Requires: pip install playwright   (Chromium resolved from PLAYWRIGHT_BROWSERS_PATH
or a sane fallback; no `playwright install` needed in the managed environment).

Exit code 0 = all checks passed, 1 = one or more failed.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import os
import socket
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _chromium_path() -> str | None:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates = []
    if base:
        candidates.append(Path(base))
    candidates.append(Path("/opt/pw-browsers"))
    for root in candidates:
        if not root.is_dir():
            continue
        for exe in sorted(root.glob("chromium-*/chrome-linux/chrome")):
            if exe.exists():
                return str(exe)
    return None  # let Playwright resolve its own download


@contextlib.contextmanager
def _serve(directory: Path):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failures.append(name)
            print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


def run(base_url: str, headed: bool) -> int:
    checks = Checks()
    demo = f"{base_url}/?demo=1"
    with sync_playwright() as p:
        launch = {"headless": not headed}
        exe = _chromium_path()
        if exe:
            launch["executable_path"] = exe
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Boot + provenance
        page.goto(demo)
        page.wait_for_selector(".article-item", timeout=20000)
        checks.check("stream loads", page.locator(".article-item").count() > 0)
        checks.check(
            "provenance chip shows snapshot",
            "Snapshot" in (page.text_content("#data-state") or ""),
        )

        # Snippet is clean prose (no literal HTML entities) — Fix 4 guard
        snips = page.locator("#article-list .snip").all_text_contents()
        joined = " ".join(snips)
        entity_leak = any(tok in joined for tok in ["&nbsp;", "&#", "&amp;", "&quot;"])
        checks.check("snippets decode HTML entities", not entity_leak,
                     "found literal entity in a snippet")

        # Matrix technique -> dossier. On wide layouts the Matrix rail is always
        # visible; on narrow layouts it lives behind a tab.
        tab = page.locator(".mobile-tab[data-pane='matrix']")
        if tab.is_visible():
            tab.click()
        page.wait_for_selector(".matrix-tech-btn", state="visible", timeout=10000)
        page.click(".matrix-tech-btn")
        page.wait_for_selector("#dossier-panel:not([hidden])", timeout=10000)
        checks.check("technique opens dossier",
                     bool((page.text_content("#dossier-title") or "").strip()))
        checks.check("dossier has query blocks",
                     page.locator("#dossier-queries .query-stack").count() > 0)

        # Hunt -> crumb
        page.goto(demo)
        page.wait_for_selector(".article-item", timeout=20000)
        page.fill("#q", "overemployment")
        page.press("#q", "Enter")
        page.wait_for_selector("#hunt-map:not([hidden])", timeout=10000)
        crumbs = page.locator("#filter-crumbs .crumb").all_text_contents()
        checks.check("hunt shows a filter crumb",
                     any("Hunt:" in c for c in crumbs))

        # Board -> extract -> report with query blocks
        page.click("#article-list .article-board-btn")
        page.click("#board-extract")
        page.wait_for_selector("#ttp-report:not([hidden])", timeout=15000)
        checks.check("extract renders hunt report",
                     page.locator("#ttp-behavior-list li").count() > 0)
        checks.check("report has run-it query blocks",
                     page.locator("#ttp-queries .query-stack").count() > 0)

        # Themes apply
        theme_ok = True
        for theme in ("cnn-lite", "midnight", "phosphor"):
            page.select_option("#theme-select", theme)
            if page.evaluate("document.documentElement.getAttribute('data-theme')") != theme:
                theme_ok = False
        checks.check("all three themes apply", theme_ok)

        # Landscape (short viewport) — Fix 2 guard: article visible, not a sliver
        for w, h, tag in ((844, 390, "iphone"), (932, 430, "promax")):
            lp = browser.new_page(viewport={"width": w, "height": h})
            lp.goto(demo)
            lp.wait_for_selector(".article-item", timeout=20000)
            geo = lp.evaluate(
                """() => {
                  const it = document.querySelector('#article-list .article-item');
                  const r = it.getBoundingClientRect();
                  const f = document.querySelector('.site-footer');
                  return { top: Math.round(r.top), h: Math.round(r.height),
                           innerH: window.innerHeight,
                           footerShown: f ? f.getBoundingClientRect().height > 0 : false };
                }"""
            )
            visible = geo["top"] < geo["innerH"] and geo["h"] > 60
            checks.check(f"landscape {tag}: first article visible", visible,
                         f"top={geo['top']} h={geo['h']} innerH={geo['innerH']}")
            checks.check(f"landscape {tag}: footer hidden", not geo["footerShown"])
            lp.close()

        checks.check("no uncaught page errors", not errors,
                     "; ".join(errors[:3]))
        browser.close()

    total = checks.passed + len(checks.failures)
    print(f"\n{checks.passed}/{total} checks passed")
    if checks.failures:
        print("FAILED: " + ", ".join(checks.failures))
        return 1
    print("UI smoke: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="Test an already-running instance instead of serving web/")
    ap.add_argument("--headed", action="store_true", help="Run with a visible browser")
    args = ap.parse_args()

    if args.url:
        return run(args.url.rstrip("/"), args.headed)
    with _serve(WEB) as base:
        return run(base, args.headed)


if __name__ == "__main__":
    sys.exit(main())
