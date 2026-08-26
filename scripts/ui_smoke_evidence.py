#!/usr/bin/env python3
"""Full Playwright journey over the EVIDENCE page (local; CI keeps its own floor).

`ui_smoke_ci.py` is the required gate and asserts only that the pane becomes
visible. This drives the controls the redesign added — collapsible finding
groups, the sortable/expandable technique table, the trend matrix, jurisdiction
slicing — at four widths against a stub API, so a refactor that leaves the DOM
in place but the behaviour broken gets caught.

The stub payloads are derived from the real ledger core on each run, so the
fixture cannot drift from the contract it is asserting.

Usage: python scripts/ui_smoke_evidence.py   (writes /tmp/pw-<width>.png)
"""

import functools
import http.server
import json
import pathlib
import sys
import threading

from playwright.sync_api import sync_playwright


def _fixture():
    """Build the stub payloads from the REAL ledger core, not a stored file.

    This used to read two hand-placed /tmp files, which silently went stale the
    moment the payload gained a field — the suite then asserted a contract the
    server no longer served. Deriving them here means the fixture cannot drift:
    a new ledger field appears in the stub the same run it appears in the API.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger
    from tests.test_evidence_ledger import _synthetic_rows

    rows = _synthetic_rows(60, year="2022") + _synthetic_rows(120, year="2023")
    for i, row in enumerate(rows[60:]):
        row["link"] += f"-y2{i}"
    # The unit fixture is single-technique, which cannot exercise the sortable
    # table or a multi-row trend matrix. Spread the rows over several
    # techniques in uneven proportions, and skew the postures differently
    # again: a fixture where cases and proven rank the same way cannot tell a
    # working sort from a broken one.
    spread = ["IF016"] * 5 + ["MT003.002"] * 4 + ["IF016.004"] * 3 + ["PV018"] * 2 + ["MT007"]
    # Proven rate per technique, chosen so that WITHIN a theme the proven
    # ranking INVERTS the case ranking: IF016.004 has fewer cases than IF016
    # but more proven ones, and MT007 fewer than MT003.002 but more proven.
    # Sorting is within theme groups, so a fixture where every column ranks the
    # same way cannot tell a working sort from a broken one.
    proven_rate = {"IF016": 8, "IF016.004": 2, "MT003.002": 6, "MT007": 2, "PV018": 4}
    seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        tech = spread[i % len(spread)]
        row["forensics"]["candidate_technique_ids"] = [tech]
        n = seen[tech] = seen.get(tech, 0) + 1
        proven = n % proven_rate[tech] == 0
        row["forensics"]["legal_posture"] = "judgment" if proven else "complaint"
        for m in row["forensics"]["methods"]:
            m["claim_status"] = "adjudicated" if proven else "alleged"
    titles = {
        "IF016": "Embezzlement",
        "IF016.004": "Insider trading",
        "MT003.002": "Exfiltration over personal email",
        "MT007": "Credential misuse",
        "PV018": "Access review",
    }
    global TITLES
    TITLES = titles
    ledger = attach_catalog_titles(build_evidence_ledger(rows, top=25), titles)
    # Each slice must be a genuinely DIFFERENT report — the suite asserts that
    # switching jurisdiction restates the numbers, and a slice that is a copy
    # of GLOBAL would let that check pass on a broken page.
    us = attach_catalog_titles(build_evidence_ledger(rows[:120], top=25), titles)
    # One thin slice, so the small-n path is exercised too.
    thin = attach_catalog_titles(build_evidence_ledger(rows[:6], top=25), titles)
    return ledger, {"US": us, "IN": thin}


TITLES: dict[str, str] = {}
LEDGER, SLICES = _fixture()

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
            # Serve the SAME technique set the ledger fixture uses. A catalog
            # that disagrees with the ledger cannot open a dossier for a row
            # the table shows, which is exactly how this stub failed silently.
            return self._j({"themes": [{"id": "ME", "label": "Means"}], "techniques": [
                {"id": t, "title": n, "theme": "means", "description": n,
                 "detections": [], "preventions": []}
                for t, n in sorted(TITLES.items())]})
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

        # --- the memo furniture (operator review 2026-08-25) -----------------
        # A findings memo opens with what it found, numbers its findings, and
        # states its shared caveats once. Repeating them per card is what
        # teaches a reader to skip exactly the warnings that matter.
        ck(tag, "bottom line paints above the first group", pg.evaluate(
            "(()=>{const b=document.getElementById('evp-bottom-line');"
            "if(!b||b.hidden||!b.textContent.trim())return false;"
            "const g=document.querySelector('.evp-finding-group');"
            "return (b.compareDocumentPosition(g)&Node.DOCUMENT_POSITION_FOLLOWING)>0;})()"))
        ck(tag, "shared caveat renders once, after the findings", pg.evaluate(
            "(()=>{const c=document.getElementById('evp-findings-caveat');"
            "if(!c||c.hidden||!c.textContent.trim())return false;"
            "const l=document.getElementById('evp-findings-list');"
            "return (l.compareDocumentPosition(c)&Node.DOCUMENT_POSITION_FOLLOWING)>0;})()"))
        nums = pg.eval_on_selector_all(".evp-finding-num", "e=>e.map(x=>x.textContent)")
        ck(tag, "findings numbered F1..Fn in document order",
           bool(nums) and nums == [f"F{i}" for i in range(1, len(nums) + 1)], str(nums))
        ck(tag, "a supporting card drops the actions block", pg.evaluate(
            "(()=>{const s=document.querySelectorAll('.evp-finding-sup');return s.length===0"
            "||[...s].every(c=>!c.querySelector('.evp-finding-actions'));})()"))
        # The whole point of the {technique} slot: no bare ITM id in prose, and
        # the code is a live link into that technique's dossier.
        ck(tag, "no raw {technique} slot reaches the reader",
           "{technique}" not in pg.inner_text("#evp-findings"))
        ck(tag, "a technique named in a finding is clickable", pg.evaluate(
            "(()=>{const b=document.querySelectorAll('#evp-findings .evp-tech-name');"
            "return b.length===0||[...b].every(x=>x.tagName==='BUTTON'"
            "&&x.textContent.trim().length>0);})()"))

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
        #
        # Re-measured 2026-08-25 against the derived fixture: the old numbers
        # were recorded from a stored /tmp fixture whose technique table was a
        # different width, so 768 and 1440 sat 2px low. Confirmed pre-existing
        # by running this suite against an unmodified web/ tree, which
        # reproduces the same widths.
        sw, cw = pg.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        baseline = {390: 502, 768: 838, 1024: 1104, 1440: 1509}.get(w)
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

    # --- cold start: the API is DOWN, the snapshot carries the page ----------
    # The regression this guards (measured 2026-08-26): with the API asleep the
    # page painted nothing at all — EVIDENCE was missing from boot()'s
    # pre-probe dispatch, so when the probe ladder threw, the route never
    # fired. Writes ledger.json into web/data/ for the duration, then removes
    # it — the smoke guard forbids committing that directory.
    import json as _json
    import shutil as _shutil

    data_dir = pathlib.Path(__file__).resolve().parent.parent / "web" / "data"
    made_dir = not data_dir.exists()
    if made_dir:
        data_dir.mkdir()
    snap = data_dir / "ledger.json"
    made_file = not snap.exists()
    if made_file:
        snap.write_text(_json.dumps(LEDGER), encoding="utf-8")
    try:
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        # Port 9 is unroutable: this is a cold Cloud Run instance, or none.
        pg.add_init_script("window.INSIDER_INTEL_API_BASE='http://127.0.0.1:9';"
                           "try{localStorage.setItem('insider-intel-guide-dismissed','1')}catch(e){}")
        # Google Fonts has no egress in CI/sandbox and the stylesheet is
        # render-blocking; without this the measurement is the font timeout,
        # not the page (12.7s of a 13s "load" traced to exactly that).
        pg.route("**://fonts.googleapis.com/**", lambda r: r.abort())
        pg.route("**://fonts.gstatic.com/**", lambda r: r.abort())
        pg.goto("http://127.0.0.1:8899/#/evidence", wait_until="domcontentloaded")
        painted = True
        try:
            pg.wait_for_selector(".evp-finding-title", timeout=20000)
        except Exception:
            painted = False
        ck("cold-start", "findings paint with the API down", painted)
        ck("cold-start", "the cached paint says CACHED and never claims LIVE",
           "CACHED" in (pg.eval_on_selector("#evp-basis-line", "e=>e.textContent") or ""))
        pg.close()
    finally:
        if made_file and snap.exists():
            snap.unlink()
        if made_dir and data_dir.exists():
            _shutil.rmtree(data_dir)

    b.close()

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  -", f)
sys.exit(1 if fails else 0)
