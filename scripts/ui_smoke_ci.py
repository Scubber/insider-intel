"""Browser-floor smoke for CI: the real web/ served statically, no API.

The full journey suite (scripts/ui_smoke.py) drives the preview bundle with
embedded data. This driver asserts the FLOOR that must hold for every PR,
against exactly what ships — web/ behind a plain static file server, with no
live API and (unless the checkout carries web/data/) no boot snapshot:

- the page boots with ZERO uncaught page errors and ZERO console errors
  beyond the expected-offline allowlist (failed fetches to the absent live
  API and the absent web/data/ snapshot),
- every masthead tab renders its pane without throwing
  (STREAM / MATRIX / EVIDENCE / TOOLING / RESEARCH / WORKBENCH / SETTINGS),
- the GUIDE opens and dismisses — via the masthead button on desktop and the
  mobile tab row's GUIDE button on phone widths (the footer reopener is gone),
- TOOLING renders its table — snapshot rows when web/data/tooling.json is
  present, else the honest "payload unreachable" teaching note,
- hash deep links (#/tooling, #/technique/IF002, #/tools, #/about, #/research
  and #/research/<slug>) don't crash, legacy #/tools re-navigates to
  #/tooling, and #/about renders the ABOUT pane with its byline,
- all of it at two viewports: 1280 desktop and 390 mobile.

On failure each viewport screenshots into ui-smoke-artifacts/ for the CI
artifact upload. Reuses scripts/ui_smoke.py's helpers (server, chromium
resolution, check ledger, drift guard) — this file adds no new assertions to
the preview suite, only the static-offline floor.

Usage:
  python scripts/ui_smoke_ci.py             # serve web/ and run headless
  python scripts/ui_smoke_ci.py --url URL   # test an already-running server
  python scripts/ui_smoke_ci.py --headed    # watch it run

Exit code 0 = all checks passed, 1 = one or more failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from scripts.ui_smoke import Checks, _chromium_path, _drift_guard, _serve  # noqa: E402

WEB = ROOT / "web"
ARTIFACTS = ROOT / "ui-smoke-artifacts"

# Console errors the static-offline run EXPECTS: the app probing its absent
# live API (config.js points non-public hosts at 127.0.0.1:8000) and the
# fail-soft fetches for the absent Pages boot snapshot (web/data/ is
# generated into the deploy artifact, never committed). External font hosts
# are tolerated for sandboxes without egress. Anything else — above all a
# script error — fails the job.
ALLOWED_CONSOLE = (
    "127.0.0.1:8000",
    "localhost:8000",
    "net::ERR_CONNECTION_REFUSED",
    "Failed to fetch",
    "/data/articles.json",
    "/data/meta.json",
    "/data/tooling.json",
    # Fourth snapshot file, same reason as the three above: this job serves
    # web/ with no snapshot, so an absent boot file is the expected offline
    # state, not a defect. EVIDENCE reads it since 2026-08-26.
    "/data/ledger.json",
    "/favicon.ico",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

# Masthead panes, in click order: STREAM last so the shell ends where it
# boots. Each entry maps data-pane -> selector that must become visible after
# the click. MATRIX differs by layout: desktop opens the takeover browser
# (#matrix-panel, inside the always-visible pane-articles); mobile switches
# to the ITM rail pane. STREAM's proof is the article panel itself — the
# pane-articles section also hosts the matrix takeover, so it proves nothing.
PANE_PROOF_COMMON = {
    "evidence": ".pane-evidence-page",
    "tooling": "#tlt-table tr",
    "research": ".pane-research-page",
    "workbench": ".pane-workbench",
    "settings": ".pane-settings",
    "articles": "#article-panel:not([hidden])",
}
PANE_PROOF_MATRIX = {
    "desktop": "#matrix-panel:not([hidden])",
    "mobile": "#matrix-panel",
}
PANE_ORDER = ("matrix", "evidence", "tooling", "research", "workbench", "settings", "articles")


def _wire_error_capture(page, console_bad: list[str], page_errors: list[str]) -> None:
    def on_console(msg):
        if msg.type != "error":
            return
        loc = msg.location or {}
        blob = f"{msg.text} {loc.get('url', '')}"
        if any(tok in blob for tok in ALLOWED_CONSOLE):
            return
        console_bad.append(blob.strip())

    page.on("console", on_console)
    page.on("pageerror", lambda err: page_errors.append(str(err)))


def _drive_viewport(browser, base_url: str, checks: Checks, width: int, height: int, tag: str):
    tab_sel = ".masthead-nav-item" if width > 960 else ".mobile-tab"
    page = browser.new_page(viewport={"width": width, "height": height})
    console_bad: list[str] = []
    page_errors: list[str] = []
    _wire_error_capture(page, console_bad, page_errors)
    failures_before = len(checks.failures)
    try:
        # Boot: shell renders. No API and no snapshot means no articles — the
        # floor is the chrome, not the data.
        page.goto(f"{base_url}/")
        page.wait_for_selector(".app-shell", state="attached", timeout=15000)
        checks.check(
            f"{tag}: shell boots with masthead + tabs",
            page.locator(".masthead-nav-item").count() >= 6
            and page.locator(f"{tab_sel}[data-pane='tooling']").count() > 0,
        )

        # GUIDE: auto-shows for a first-time visitor; Esc dismisses. On
        # desktop the masthead GUIDE button must reopen it.
        page.wait_for_selector("#guide-panel:not([hidden])", timeout=10000)
        checks.check(f"{tag}: guide auto-shows on first visit", True)
        page.keyboard.press("Escape")
        checks.check(
            f"{tag}: Esc dismisses the guide",
            page.locator("#guide-panel[hidden]").count() == 1,
        )
        if width > 960:
            page.click("#guide-open")
            checks.check(
                f"{tag}: GUIDE reopens the guide",
                page.is_visible("#guide-panel"),
            )
            page.keyboard.press("Escape")
        else:
            # Phone widths: the masthead nav is display:none — the mobile tab
            # row's GUIDE button is the reopener (footer removal, 2026-08-17).
            page.click("#mobile-guide")
            checks.check(
                f"{tag}: mobile GUIDE tab reopens the guide",
                page.is_visible("#guide-panel"),
            )
            page.keyboard.press("Escape")

        # Every tab renders its pane without throwing.
        layout = "desktop" if width > 960 else "mobile"
        for pane in PANE_ORDER:
            page.click(f"{tab_sel}[data-pane='{pane}']")
            proof = PANE_PROOF_MATRIX[layout] if pane == "matrix" else PANE_PROOF_COMMON[pane]
            try:
                page.wait_for_selector(proof, state="visible", timeout=10000)
                ok = True
            except Exception:
                ok = False
            checks.check(f"{tag}: {pane.upper()} tab renders its pane", ok)

        # TOOLING content: with a snapshot in the checkout the grouped table
        # must carry real rows; without one, the honest unreachable note.
        page.click(f"{tab_sel}[data-pane='tooling']")
        page.wait_for_selector("#tlt-table tr", state="visible", timeout=10000)
        if (WEB / "data" / "tooling.json").exists():
            checks.check(
                f"{tag}: TOOLING renders snapshot rows",
                page.locator("#tlt-table th").count() > 0,
            )
        else:
            note = page.text_content("#tlt-table") or ""
            checks.check(
                f"{tag}: TOOLING shows its offline teaching note",
                "unreachable" in note or page.locator("#tlt-table th").count() > 0,
                f"table text={note[:80]!r}",
            )

        # Hash deep links via the in-page router: none may crash, and legacy
        # #/tools must re-navigate to the one TOOLING table.
        for link in (
            "#/technique/IF002",
            "#/tools",
            "#/about",
            "#/research",
            "#/research/danger-profiles-2026-08",
            "#/research/email-destinations-2026-08",
            "#/research/fs-insider-profiles-2026-09",
            "#/",
        ):
            page.evaluate(f"() => {{ location.hash = '{link}'; }}")
            page.wait_for_timeout(400)
            checks.check(
                f"{tag}: deep link {link} doesn't crash",
                not page_errors,
                "; ".join(page_errors[:2]),
            )
            if link == "#/tools":
                checks.check(
                    f"{tag}: legacy #/tools lands on #/tooling",
                    page.evaluate("() => location.hash") == "#/tooling",
                )
            if link == "#/about":
                # ABOUT is static prose: the pane must render offline with the
                # byline and attribution. Whitespace-normalized — markup may
                # break the phrase across an inline link.
                about_text = " ".join((page.text_content(".pane-about-page") or "").split())
                checks.check(
                    f"{tag}: #/about renders the ABOUT pane with its byline",
                    page.is_visible(".pane-about-page")
                    and "Built and run by Tim Carreira" in about_text
                    and "Forscie" in about_text,
                    f"text={about_text[:80]!r}",
                )

        # Cold-load deep links: a fresh boot straight onto each route (via
        # about:blank — a same-document hash hop would skip the reload).
        for link in (
            "#/tooling",
            "#/technique/IF002",
            "#/about",
            "#/research",
            "#/research/danger-profiles-2026-08",
            "#/research/email-destinations-2026-08",
            "#/research/fs-insider-profiles-2026-09",
        ):
            page.goto("about:blank")
            page.goto(f"{base_url}/{link}")
            page.wait_for_selector(".app-shell", state="attached", timeout=15000)
            page.wait_for_timeout(400)
            checks.check(
                f"{tag}: cold boot on {link} doesn't crash",
                not page_errors,
                "; ".join(page_errors[:2]),
            )

        checks.check(
            f"{tag}: no uncaught page errors",
            not page_errors,
            "; ".join(page_errors[:3]),
        )
        checks.check(
            f"{tag}: no console errors beyond the offline allowlist",
            not console_bad,
            "; ".join(console_bad[:3]),
        )
    except Exception as exc:
        # A blown wait/click is a real failure, not a harness crash: record it,
        # screenshot below, and let the other viewport still run.
        checks.check(
            f"{tag}: smoke drive completed",
            False,
            f"{type(exc).__name__}: {exc}".split("\n")[0],
        )
    finally:
        if len(checks.failures) > failures_before:
            ARTIFACTS.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(ARTIFACTS / f"{tag}.png"), full_page=True)
                print(f"  saved failure screenshot: ui-smoke-artifacts/{tag}.png")
            except Exception as exc:  # screenshotting must never mask the failure
                print(f"  screenshot failed: {exc}")
        page.close()


def run(base_url: str, headed: bool) -> int:
    checks = Checks()
    _drift_guard(checks)
    with sync_playwright() as p:
        launch = {"headless": not headed}
        exe = _chromium_path()
        if exe:
            launch["executable_path"] = exe
        browser = p.chromium.launch(**launch)
        _drive_viewport(browser, base_url, checks, 1280, 800, "desktop-1280")
        _drive_viewport(browser, base_url, checks, 390, 844, "mobile-390")
        browser.close()

    total = checks.passed + len(checks.failures)
    print(f"\n{checks.passed}/{total} checks passed")
    if checks.failures:
        print("FAILED: " + ", ".join(checks.failures))
        return 1
    print("UI smoke (CI floor): OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="Test a running instance instead of serving web/")
    ap.add_argument("--headed", action="store_true", help="Run with a visible browser")
    args = ap.parse_args()
    if args.url:
        return run(args.url.rstrip("/"), args.headed)
    with _serve(WEB) as base:
        return run(base, args.headed)


if __name__ == "__main__":
    sys.exit(main())
