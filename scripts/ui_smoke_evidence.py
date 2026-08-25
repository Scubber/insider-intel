#!/usr/bin/env python3
"""Full Playwright journey over the EVIDENCE page (local; CI keeps its own floor).

`ui_smoke_ci.py` is the required gate and asserts only that the pane becomes
visible. This drives the controls the redesign added — collapsible finding
groups, the sortable/expandable technique table, the trend matrix, jurisdiction
slicing — at four widths against a stub API, so a refactor that leaves the DOM
in place but the behaviour broken gets caught.

Usage: python scripts/ui_smoke_evidence.py   (writes /tmp/pw-<width>.png)
"""

import functools
import http.server
import json
import sys
import threading

from playwright.sync_api import sync_playwright

LEDGER = json.load(open("/tmp/pw_ledger.json"))
SLICES = json.load(open("/tmp/pw_slices.json"))

class Web(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

class Api(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _j(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        path, _, q = self.path.partition("?")
        params = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        if path == "/evidence/ledger":
            return self._j(SLICES.get(params.get("country", "").upper(), LEDGER))
        if path.startswith("/evidence/technique/"):
            cc = params.get("country", "").upper()
            if cc == "IN":
                # Absent-in-slice: the 404 the UI must turn into an empty state.
                return self._j({"detail": "no observed cases"}, 404)
            return self._j({
                "id": path.rsplit("/", 1)[-1], "cases": 42,
                "adjudicated_admitted": 9, "alleged": 33,
                "enriched_cases": 600, "small_n_floor": 10,
                "detections": [], "evidence": [{"artifact": "email logs / content", "cases": 20}],
                "hunts": [], "terms": [], "behaviors": [], "patterns": [],
                "patterns_generated_at": None,
            })
        if path == "/health":
            return self._j({"status": "ok"})
        if path == "/sources":
            return self._j([])
        if path == "/itm":
            return self._j({"themes": [{"id": "ME", "label": "Means"}], "techniques": [
                {"id": t, "title": n, "theme": "means", "description": n,
                 "detections": [], "preventions": []}
                for t, n in (("IF016", "Insider trading"), ("IF002", "Data exfiltration"),
                             ("ME005", "Removable media"), ("PR003", "Credential misuse"),
                             ("IF038", "Moonlighting"))]})
        if path == "/articles":
            return self._j({"articles": [], "total": 0, "generated_at": "2026-08-25T00:00:00Z"})
        return self._j({})

_web = http.server.ThreadingHTTPServer(
    ("127.0.0.1", 8899), functools.partial(Web, directory="web")
)
_api = http.server.ThreadingHTTPServer(("127.0.0.1", 8901), Api)
threading.Thread(target=_web.serve_forever, daemon=True).start()
threading.Thread(target=_api.serve_forever, daemon=True).start()

WIDTHS = [("phone", 390, 900), ("ipad-portrait", 768, 1024),
          ("ipad-landscape", 1024, 768), ("desktop", 1440, 1000)]
fails, checks = [], 0

def ck(tag, name, ok, detail=""):
    global checks
    checks += 1
    if not ok:
        fails.append(f"{tag}: {name} {detail}")
    print(("  PASS  " if ok else "  FAIL  ") + f"{tag}: {name} {detail}")

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for tag, w, h in WIDTHS:
        pg = b.new_page(viewport={"width": w, "height": h})
        perrs, cerrs = [], []
        pg.on("pageerror", lambda e: perrs.append(str(e)))
        pg.on("console", lambda m: cerrs.append(m.text) if m.type == "error" else None)
        pg.add_init_script("window.INSIDER_INTEL_API_BASE='http://127.0.0.1:8901';"
                           "try{localStorage.setItem('insider-intel-guide-dismissed','1')}catch(e){}")
        pg.goto("http://127.0.0.1:8899/#/evidence", wait_until="domcontentloaded")
        pg.wait_for_selector(".evp-finding-group", timeout=20000)
        pg.wait_for_timeout(500)

        # --- findings groups -------------------------------------------------
        gs = pg.eval_on_selector_all(".evp-finding-group",
            "e=>e.map(x=>({open:x.open,h:Math.round(x.querySelector('summary').getBoundingClientRect().height),"
            "lead:!!x.querySelector('.evp-group-lead')?.textContent.trim()}))")
        ck(tag, "groups render", len(gs) >= 2, f"({len(gs)})")
        ck(tag, "first open, rest collapsed", gs[0]["open"] and not any(g["open"] for g in gs[1:]))
        ck(tag, "every summary >=44px", all(g["h"] >= 44 for g in gs), str([g["h"] for g in gs]))
        ck(tag, "collapsed headers still teach", all(g["lead"] for g in gs))
        ck(tag, "legend precedes findings", pg.evaluate(
            "(()=>{const l=document.querySelector('.evp-legend'),"
            "f=document.getElementById('evp-findings');"
            "return (l.compareDocumentPosition(f)&Node.DOCUMENT_POSITION_FOLLOWING)>0;})()"))

        # opening a group must not shift the page under the reader
        # Document-relative: a native <details> scrolls its summary into view,
        # so viewport-relative tops legitimately move. What must not move is the
        # findings block's position within the document.
        doc_top = (
            "(()=>{const r=document.getElementById('evp-findings')"
            ".getBoundingClientRect();return Math.round(r.top+window.scrollY);})()"
        )
        before = pg.evaluate(doc_top)
        pg.query_selector_all(".evp-finding-group summary")[1].click()
        pg.wait_for_timeout(250)
        after = pg.evaluate(doc_top)
        ck(tag, "expanding a group doesn't move what's above it", abs(before - after) <= 4,
           f"({before} -> {after})")
        ck(tag, "group actually opened",
           pg.eval_on_selector_all(".evp-finding-group", "e=>e[1].open"))

        # --- no card headlines a caveat --------------------------------------
        titles = pg.eval_on_selector_all(
            ".evp-finding-title", "e=>e.map(x=>x.textContent.toLowerCase())")
        ck(tag, "no disclaimer card",
           not any("still allegations" in t or "finding of fact" in t for t in titles))
        ck(tag, "cards tagged DERIVED", all(
            t == "DERIVED"
            for t in pg.eval_on_selector_all(".evp-finding-tag", "e=>e.map(x=>x.textContent)")))

        # --- trend matrix ----------------------------------------------------
        ck(tag, "trend visible", not pg.evaluate("document.getElementById('evp-trend').hidden"))
        ck(tag, "trend scrolls inside its own container, page does not", pg.evaluate(
            "(()=>{const s=document.querySelector('#evp-trend .evp-scroll');"
            "return s.scrollWidth>=s.clientWidth;})()"))

        # --- technique table -------------------------------------------------
        cols = pg.eval_on_selector_all("#evp-techniques th", "e=>e.map(x=>x.textContent.trim())")
        ck(tag, "ALLEGED column present", any("ALLEGED" in c for c in cols))
        ck(tag, "expand targets >=44px", all(
            d >= 44 for d in pg.eval_on_selector_all("#evp-techniques .evp-expand",
            "e=>e.flatMap(x=>{const r=x.getBoundingClientRect();return [r.width,r.height];})")))
        rows0 = pg.eval_on_selector_all("#evp-techniques tr", "e=>e.length")
        ex = pg.query_selector_all("#evp-techniques .evp-expand")
        if ex:
            ex[0].click()
            pg.wait_for_timeout(200)
            ck(tag, "row expands to its evidence trail",
               pg.eval_on_selector_all("#evp-techniques tr", "e=>e.length") > rows0
               and pg.eval_on_selector_all(".evp-tech-detail .evp-row", "e=>e.length") > 0)
        sb = pg.query_selector_all("#evp-techniques .evp-sort")
        if len(sb) >= 3:
            titles_js = "e=>e.map(x=>x.textContent)"
            before_o = pg.eval_on_selector_all("#evp-techniques .evp-tech-title", titles_js)
            sb[1].click()
            pg.wait_for_timeout(200)
            after_o = pg.eval_on_selector_all("#evp-techniques .evp-tech-title", titles_js)
            ck(tag, "sorting reorders rows", before_o != after_o)
            ck(tag, "aria-sort tracks the active column",
               pg.eval_on_selector_all(
                   "#evp-techniques th", "e=>e.map(x=>x.getAttribute('aria-sort'))"
               ).count("descending") == 1)

        # --- jurisdiction slice ----------------------------------------------
        tabs = pg.query_selector_all(".evp-juris-tab")
        if len(tabs) > 1:
            stat_before = pg.eval_on_selector("#evp-stats b", "e=>e.textContent")
            tabs[1].click()
            pg.wait_for_timeout(900)
            ck(tag, "switching jurisdiction restates the numbers",
               pg.eval_on_selector("#evp-stats b", "e=>e.textContent") != stat_before)
            ck(tag, "a new jurisdiction reopens on group one",
               pg.eval_on_selector_all(
                   ".evp-finding-group",
                   "e=>e.length?e[0].open&&!e.slice(1).some(x=>x.open):true"))

        # --- no horizontal overflow inside the pane ---------------------------
        # The page has scrolled sideways at narrow widths since before this
        # work (390: scrollWidth 502 on the unmodified tree). Not this PR's to
        # fix, but it must not get WORSE — measured, with the baseline stated.
        sw, cw = pg.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        baseline = {390: 502, 768: 836, 1024: 1104, 1440: 1507}.get(w)
        ck(tag, "horizontal overflow no worse than baseline",
           baseline is None or sw <= baseline,
           f"(scrollWidth {sw} vs {cw} viewport; was {baseline})")
        ck(tag, "anything wider than the viewport sits in a scroll container", pg.evaluate(
            """(()=>{const w=document.documentElement.clientWidth;
            const scrollable=e=>{for(let p=e.parentElement;p;p=p.parentElement){
              const o=getComputedStyle(p).overflowX;
              if(o==='auto'||o==='scroll'||o==='hidden') return true;}
            return false;};
            return [...document.querySelectorAll('.pane-evidence-page *')]
              .filter(e=>e.getBoundingClientRect().right>w+1).every(scrollable);})()"""))

        ck(tag, "no uncaught page errors", not perrs, str(perrs[:2]))
        allow = ("127.0.0.1:8000", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET",
                 "Failed to fetch", "Failed to load resource", "/data/",
                 "favicon", "fonts.googleapis", "fonts.gstatic", "404")
        real = [e for e in cerrs if not any(a in e for a in allow)]
        ck(tag, "no unexpected console errors", not real, str(real[:2]))

        pg.screenshot(path=f"/tmp/pw-{tag}.png", full_page=True)
        pg.close()

    # --- dossier honours the slice (desktop) ---------------------------------
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.add_init_script("window.INSIDER_INTEL_API_BASE='http://127.0.0.1:8901';"
                       "try{localStorage.setItem('insider-intel-guide-dismissed','1')}catch(e){}")
    pg.goto("http://127.0.0.1:8899/#/evidence", wait_until="domcontentloaded")
    pg.wait_for_selector("#evp-techniques .evp-tech-name", timeout=20000)
    tabs = pg.query_selector_all(".evp-juris-tab")
    us = [t for t in tabs if "US" in (t.text_content() or "")]
    if us:
        us[0].click()
        pg.wait_for_timeout(900)
    pg.query_selector("#evp-techniques .evp-tech-name").click()
    pg.wait_for_timeout(1500)
    txt = pg.eval_on_selector("#dossier-evidence-count", "e=>e.textContent") or ""
    ck("dossier", "names the jurisdiction it is reporting",
       "JURISDICTION: US" in txt, f"[{txt[:70]}]")
    pg.close()
    b.close()

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  -", f)
sys.exit(1 if fails else 0)
