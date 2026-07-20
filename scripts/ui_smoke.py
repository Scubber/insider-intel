"""Headless UX smoke test for the insider-intel web UI.

Builds the standalone preview bundle (self-contained, no API) and drives the
core investigator journeys in a real browser, asserting each works — plus a
guard that no demo/offline code has re-entered web/. Run after any change to web/.

Usage:
  python scripts/ui_smoke.py            # build the preview and run
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


def _drift_guard(checks: Checks) -> None:
    """The shipped site (web/) must carry no demo/offline code — the preview is
    the only place that lives. Guard against re-introducing the entanglement."""
    banned = ("demo-store", "INSIDER_INTEL_DEMO", "InsiderIntelDemo", "?demo")
    hits = []
    for name in ("index.html", "app.js", "config.js"):
        text = (WEB / name).read_text(encoding="utf-8")
        for tok in banned:
            if tok in text:
                hits.append(f"{name}:{tok}")
    checks.check("web/ has no demo code", not hits, ", ".join(hits))
    checks.check("web/demo/ is gone", not (WEB / "demo").exists())


def run(base_url: str, headed: bool) -> int:
    checks = Checks()
    demo = f"{base_url}/"
    _drift_guard(checks)
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
            "data-state chip renders",
            "articles" in (page.text_content("#data-state") or ""),
        )

        # Intro banner: expanded on a fresh profile (no localStorage), GOT IT
        # minimizes to the header bar, the state persists across reloads
        # (returning profile), and the masthead ? re-expands it.
        checks.check(
            "intro banner shows on first visit",
            page.is_visible("#intro-body") and page.is_visible("#intro-gotit"),
        )
        page.click("#intro-gotit")
        checks.check(
            "GOT IT minimizes the intro (header bar stays)",
            not page.is_visible("#intro-body") and page.is_visible(".intro-head"),
        )
        page.reload()
        page.wait_for_selector(".article-item", timeout=20000)
        checks.check(
            "intro stays minimized for returning visitors",
            not page.is_visible("#intro-body"),
        )
        page.click("#intro-help")
        checks.check("? reopens the intro", page.is_visible("#intro-body"))
        page.click("#intro-gotit")

        # TRENDING panel: real rows (or the honest empty state) — never the
        # old TODO placeholder.
        trend_rows = page.locator(".pane-trending .trend-row").count()
        trend_empty = page.locator(".pane-trending .trending-empty").count()
        pane_text = page.text_content(".pane-trending") or ""
        checks.check(
            "trending panel renders rows or empty state",
            (trend_rows > 0 or trend_empty > 0) and "TODO" not in pane_text,
            f"rows={trend_rows} empty={trend_empty}",
        )
        if trend_rows:
            first = page.text_content(".pane-trending .trend-row .trend-delta") or ""
            checks.check(
                "trending rows carry a delta chip",
                any(tok in first for tok in ("↑", "↓", "NEW", "±")),
                f"delta={first!r}",
            )

        # Snippet is clean prose (no literal HTML entities) — Fix 4 guard
        snips = page.locator("#article-list .snip").all_text_contents()
        joined = " ".join(snips)
        entity_leak = any(tok in joined for tok in ["&nbsp;", "&#", "&amp;", "&quot;"])
        checks.check(
            "snippets decode HTML entities", not entity_leak, "found literal entity in a snippet"
        )

        # Long analyst notes are sentence-chunked into short paragraphs, not
        # one wall of text (chunks are built at render time, even clamped).
        chunked = page.evaluate(
            """() => {
              const rows = [...document.querySelectorAll('#article-list .article-row')];
              const row = rows.find(
                (r) => ((r.querySelector('.snip') || {}).textContent || '').length > 300
              );
              return row ? row.querySelectorAll('.snip-para').length : -1;
            }"""
        )
        checks.check("long notes are sentence-chunked", chunked >= 2, f"snip-para count={chunked}")

        # Observed-only ITM rail: grouped theme headers present, and the rail
        # never renders zero-coverage taxonomy rows.
        theme_headers = page.locator(".pane-matrix .itm-rail-theme").count()
        checks.check(
            "rail shows observed theme groups",
            1 <= theme_headers <= 5,
            f"{theme_headers} theme headers",
        )
        checks.check(
            "rail has no zero-coverage rows",
            page.locator(".pane-matrix .matrix-tech-zero").count() == 0,
        )

        # Case click collapses the rail to that article's tagged techniques.
        full_rows = page.locator(".itm-rail-btn").count()
        row_with_hit = page.locator(".article-row:has(.itm-id-chip)").first
        row_with_hit.locator(".article-item").click()
        case_rows = page.locator(".itm-rail-btn").count()
        case_hits = page.locator(".itm-rail-btn.case-hit").count()
        checks.check(
            "selected case filters rail to its techniques",
            1 <= case_rows < full_rows and case_hits == case_rows,
            f"full={full_rows} case={case_rows} hits={case_hits}",
        )
        page.click("#itm-rail-show-all")
        checks.check(
            "SHOW ALL restores the full observed rail",
            page.locator(".itm-rail-btn").count() == full_rows,
        )

        # Masthead MATRIX opens the center full-matrix browser and comes back.
        page.click(".masthead-nav-item[data-pane='matrix']")
        page.wait_for_selector("#matrix-panel:not([hidden])", timeout=10000)
        checks.check(
            "MATRIX nav opens full matrix",
            page.locator("#matrix-columns .matrix-col").count() == 5
            and page.locator("#matrix-q").is_visible(),
        )
        page.click("#matrix-back")
        page.wait_for_selector("#article-panel:not([hidden])", timeout=10000)

        # Matrix technique -> dossier. On wide layouts the observed-only rail is
        # always visible; on narrow layouts it lives behind a tab.
        tab = page.locator(".mobile-tab[data-pane='matrix']")
        if tab.is_visible():
            tab.click()
        page.wait_for_selector(".matrix-tech-btn", state="visible", timeout=10000)
        page.click(".matrix-tech-btn")
        page.wait_for_selector("#dossier-panel:not([hidden])", timeout=10000)
        checks.check(
            "technique opens dossier", bool((page.text_content("#dossier-title") or "").strip())
        )
        checks.check(
            "dossier has query blocks", page.locator("#dossier-queries .query-stack").count() > 0
        )

        # Hunt -> crumb
        page.goto(demo)
        page.wait_for_selector(".article-item", timeout=20000)
        page.fill("#q", "overemployment")
        page.press("#q", "Enter")
        page.wait_for_selector("#hunt-map:not([hidden])", timeout=10000)
        crumbs = page.locator("#filter-crumbs .crumb").all_text_contents()
        checks.check("hunt shows a filter crumb", any("Hunt:" in c for c in crumbs))

        # Board -> extract -> report with query blocks. Board a row that carries
        # ITM evidence so the v2 per-technique sections have something to show.
        itm_row_btn = page.locator(
            "#article-list .article-row:has(.itm-id-chip) .article-board-btn"
        )
        if itm_row_btn.count():
            itm_row_btn.first.click()
        else:
            page.click("#article-list .article-board-btn")
        page.click("#board-extract")
        page.wait_for_selector("#ttp-report:not([hidden])", timeout=15000)
        checks.check(
            "extract renders hunt report", page.locator("#ttp-behavior-list li").count() > 0
        )
        checks.check(
            "report has run-it query blocks", page.locator("#ttp-queries .query-stack").count() > 0
        )
        # Report v2: boards with ITM evidence get per-technique case sections.
        checks.check(
            "report has per-technique case sections",
            page.locator("#ttp-technique-sections .ttp-technique .ttp-case-bullets li").count() > 0,
        )
        checks.check(
            "report shows analyst summary",
            bool((page.text_content("#ttp-summary") or "").strip()),
        )
        # Channel chip reads "Cases" (display label; API param stays filings).
        checks.check(
            "channel chip labeled Cases",
            (page.text_content("#channel-filters [data-channel='filings']") or "").strip()
            == "Cases",
        )

        # Themes apply. The picker now lives on the Settings pane — open it
        # first, as a user would (also proves the Settings nav works).
        page.click(".masthead-nav-item[data-pane='settings']")
        page.wait_for_selector(".pane-settings", state="visible", timeout=10000)
        theme_ok = True
        for theme in ("cnn-lite", "midnight", "phosphor", "diablo"):
            page.select_option("#theme-select", theme)
            if page.evaluate("document.documentElement.getAttribute('data-theme')") != theme:
                theme_ok = False
        checks.check("themes apply from Settings (incl. midnight + cnn-lite)", theme_ok)
        page.select_option("#theme-select", "cnn-lite")

        # Settings pane renders every section from the design handoff.
        section_keys = page.evaluate(
            """() => [...document.querySelectorAll('.pane-settings .settings-section')]
                 .map((s) => s.dataset.panelKey)"""
        )
        expected = ["look", "defaults", "sources", "notify", "social", "pubs"]
        checks.check(
            "settings pane renders all sections",
            all(k in section_keys for k in expected),
            f"got {section_keys}",
        )
        # Settings sections collapse to their headers and persist.
        page.click(".settings-section[data-panel-key='look'] .panel-collapse")
        look_collapsed = page.evaluate(
            """() => document.querySelector(".settings-section[data-panel-key='look']")
                 .classList.contains('collapsed')"""
        )
        checks.check("settings section collapses", bool(look_collapsed))
        page.click(".settings-section[data-panel-key='look'] .panel-collapse")

        # Back to the stream for the panel-chrome checks.
        page.click(".masthead-nav-item[data-pane='articles']")
        page.wait_for_selector(".article-item", state="visible", timeout=10000)

        # Every stream panel collapses via its − button and hides/restores via
        # the PANELS checkbox row.
        panel_sel = {
            "core": ".pane-trending",
            "itm": ".pane-matrix",
            "stream": ".pane-articles",
            "wb": ".pane-workbench",
        }
        for key, sel in panel_sel.items():
            page.click(f"{sel} .panel-collapse[data-panel='{key}']")
            collapsed = page.evaluate(
                f"() => document.querySelector(\"{sel}\").classList.contains('collapsed')"
            )
            page.click(f"{sel} .panel-collapse[data-panel='{key}']")
            restored = page.evaluate(
                f"() => !document.querySelector(\"{sel}\").classList.contains('collapsed')"
            )
            checks.check(f"panel {key} collapses and re-expands", collapsed and restored)

            page.click(f".panel-toggle[data-panel-toggle='{key}']")
            hidden = page.evaluate(
                f"() => document.querySelector('.app-shell').classList.contains('hide-{key}')"
            ) and not page.is_visible(sel)
            page.click(f".panel-toggle[data-panel-toggle='{key}']")
            shown = page.is_visible(sel)
            checks.check(f"PANELS row hides and restores {key}", hidden and shown)

        # Collapse + hide state persists across reloads (returning profile).
        page.click(".pane-matrix .panel-collapse[data-panel='itm']")
        page.click(".panel-toggle[data-panel-toggle='core']")
        page.reload()
        page.wait_for_selector(".article-item", timeout=20000)
        persisted = page.evaluate(
            """() => document.querySelector('.pane-matrix').classList.contains('collapsed')
                 && document.querySelector('.app-shell').classList.contains('hide-core')"""
        )
        checks.check("panel collapse/hide state persists across reload", bool(persisted))
        page.click(".pane-matrix .panel-collapse[data-panel='itm']")
        page.click(".panel-toggle[data-panel-toggle='core']")

        # Signal slider filters the stream: SIG ≥ 100 should clear (or shrink)
        # it and update the refine summary; sliding back restores it. Start
        # from a clean latest stream (the earlier hunt left search mode on).
        page.goto(demo)
        page.wait_for_selector(".article-item", timeout=20000)
        base_rows = page.locator("#article-list .article-row").count()
        page.evaluate(
            """() => {
              const s = document.getElementById('signal-slider');
              s.value = '100';
              s.dispatchEvent(new Event('input', { bubbles: true }));
              s.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        page.wait_for_timeout(600)
        high_rows = page.locator("#article-list .article-row").count()
        summary = page.text_content("#refine-state") or ""
        checks.check(
            "signal slider filters the stream",
            high_rows < base_rows and "SIG ≥ 100" in summary,
            f"base={base_rows} high={high_rows} summary={summary!r}",
        )
        page.evaluate(
            """() => {
              const s = document.getElementById('signal-slider');
              s.value = '15';
              s.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        page.wait_for_timeout(600)

        # Workbench nav tab takes over full-width (stream + rail hidden) and
        # shows the explainer; MODUS OPERANDI opens the report from there.
        page.click(".masthead-nav-item[data-pane='workbench']")
        wb_takeover = (
            page.evaluate("() => document.querySelector('.app-shell').dataset.pane") == "workbench"
            and not page.is_visible(".pane-articles")
            and page.is_visible("#wb-intro")
        )
        checks.check("workbench tab opens full-width with explainer", wb_takeover)
        page.click("#board-extract")
        page.wait_for_selector("#ttp-report:not([hidden])", timeout=15000)
        checks.check(
            "MODUS OPERANDI opens the report",
            page.is_visible("#ttp-report")
            and "MODUS OPERANDI" in (page.text_content("#ttp-report h3") or ""),
        )
        page.click("#report-back")

        # Board ⋯ menu toggles and carries the share/export/import items.
        page.click(".masthead-nav-item[data-pane='workbench']")
        page.click("#board-menu-btn")
        menu_items = page.locator("#board-menu .board-menu-item").all_text_contents()
        menu_ok = page.is_visible("#board-menu") and any("SHARE LINK" in t for t in menu_items)
        page.click("#board-menu-btn")
        checks.check("board overflow menu opens with share/export items", menu_ok)
        page.click(".masthead-nav-item[data-pane='articles']")

        # Mobile journeys: full-matrix CTA from the rail tab, and tap-to-read.
        mp = browser.new_page(viewport={"width": 390, "height": 844})
        mp.goto(demo)
        mp.wait_for_selector(".article-item", timeout=20000)
        mp.click(".mobile-tab[data-pane='matrix']")
        mp.wait_for_selector("#matrix-browse-all", state="visible", timeout=10000)
        mp.click("#matrix-browse-all")
        mp.wait_for_selector("#matrix-panel:not([hidden])", timeout=10000)
        cta_ok = mp.evaluate(
            "() => document.querySelector('.app-shell').dataset.pane === 'articles'"
        )
        checks.check("mobile: rail CTA opens full matrix on articles pane", cta_ok)
        mp.click("#matrix-back")
        mp.wait_for_selector("#article-panel:not([hidden])", timeout=10000)
        expandable = mp.locator(".article-row:has(.article-expand-btn)").first
        expandable.locator(".article-item").click()
        checks.check(
            "mobile: tapping a case expands the analyst note",
            "expanded" in (expandable.get_attribute("class") or ""),
        )
        mp.close()

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
            checks.check(
                f"landscape {tag}: first article visible",
                visible,
                f"top={geo['top']} h={geo['h']} innerH={geo['innerH']}",
            )
            checks.check(f"landscape {tag}: footer hidden", not geo["footerShown"])
            lp.close()

        # Mid-width (~900–1000px): the masthead must not force horizontal
        # scroll (nav wrapping was a known issue) and the nav stays reachable
        # where it renders (>960px; below that the mobile tabs take over).
        for w in (1000, 900):
            wp = browser.new_page(viewport={"width": w, "height": 800})
            wp.goto(demo)
            wp.wait_for_selector(".article-item", timeout=20000)
            overflow = wp.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            checks.check(f"{w}px: no horizontal overflow", overflow <= 1, f"overflow={overflow}px")
            if w > 960:
                nav_ok = wp.evaluate(
                    """() => {
                      const items = [...document.querySelectorAll('.masthead-nav-item')];
                      return items.length >= 4 && items.every((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.right <= window.innerWidth + 1;
                      });
                    }"""
                )
                checks.check(f"{w}px: masthead nav items all on-screen", bool(nav_ok))
            wp.close()

        checks.check("no uncaught page errors", not errors, "; ".join(errors[:3]))
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
    ap.add_argument("--url", help="Test a running instance instead of building the preview")
    ap.add_argument("--headed", action="store_true", help="Run with a visible browser")
    args = ap.parse_args()

    if args.url:
        return run(args.url.rstrip("/"), args.headed)

    # Build the standalone preview bundle (self-contained, no API) and drive it.
    import tempfile

    from scripts.export_preview import build

    html, _kept, _total = build(article_cap=300, blob_cap=800)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "index.html").write_text(html, encoding="utf-8")
        with _serve(Path(tmp)) as base:
            return run(base, args.headed)


if __name__ == "__main__":
    sys.exit(main())
