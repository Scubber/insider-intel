"""Bundle the web UI into one self-contained PREVIEW HTML file.

Produces a single .html that runs the *unmodified* shipped web UI offline: the
app talks to its normal API, but the bundle intercepts fetch and answers from
an embedded snapshot (preview/data). The website itself has no demo/offline
code — this preview is the only place that logic lives.

Run from insider-intel/:
  python -m scripts.export_preview [--out dist/insider-intel-demo.html]
                                   [--articles 300] [--blob-cap 800]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PREVIEW = ROOT / "preview"

# Runs before config.js. Pins the API base to a sentinel and routes every call
# to it through the embedded offline responder, so app.js needs no demo code.
BOOTSTRAP = r"""
(function () {
  // Artifact viewers stamp data-theme="dark"/"light"; map onto app theme names.
  var MAP = { dark: "midnight", light: "cnn-lite" };
  new MutationObserver(function () {
    var v = document.documentElement.getAttribute("data-theme");
    if (MAP[v]) {
      document.documentElement.setAttribute("data-theme", MAP[v]);
      try {
        localStorage.setItem("insider-intel-theme", MAP[v]);
        var sel = document.getElementById("theme-select");
        if (sel) sel.value = MAP[v];
      } catch (e) {}
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
(function () {
  var BASE = "https://offline.invalid";
  window.INSIDER_INTEL_API_BASE = BASE; // config.js keeps a pre-set value
  var origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = String(input && input.url ? input.url : input);
    if (url.indexOf(BASE) !== 0) return origFetch(input, init);
    var u = new URL(url);
    var params = {};
    u.searchParams.forEach(function (v, k) { params[k] = v; });
    var opts = { method: (init && init.method) || "GET", body: init && init.body };
    return Promise.resolve(window.InsiderIntelOffline.request(u.pathname, params, opts))
      .then(function (data) {
        return new Response(data == null ? "" : JSON.stringify(data), {
          status: data == null ? 204 : 200,
          headers: { "Content-Type": "application/json" },
        });
      })
      .catch(function (err) {
        return new Response(JSON.stringify({ error: String(err) }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      });
  };
})();
"""


def _read_web(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _json_script(el_id: str, text: str) -> str:
    # Keep </script> (and comment openers) from terminating the block.
    safe = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    return f'<script id="{el_id}" type="application/json">{safe}</script>'


def _inline(path: Path) -> str:
    return f"<script>\n{path.read_text(encoding='utf-8')}\n</script>"


def build(article_cap: int, blob_cap: int) -> tuple[str, int, int]:
    articles_data = json.loads((PREVIEW / "data/articles.json").read_text())
    rows = articles_data.get("articles", [])
    rows.sort(key=lambda r: r.get("published") or "", reverse=True)
    trimmed = rows[:article_cap]
    for row in trimmed:
        blob = row.get("topic_blob") or ""
        if len(blob) > blob_cap:
            row["topic_blob"] = blob[:blob_cap]
    articles_json = json.dumps(
        {"total_indexed": len(trimmed), "articles": trimmed}, separators=(",", ":")
    )

    manifest = json.loads((PREVIEW / "data/manifest.json").read_text())
    manifest["article_count"] = len(trimmed)
    manifest["note"] = "Standalone preview bundle — embedded snapshot, not live ingest."

    html = _read_web("index.html")
    head, rest = html.split("<body>", 1)
    body = rest.rsplit("</body>", 1)[0]
    body = re.sub(r'\s*<script src="\./[^"]+"></script>', "", body)

    theme_snippet = re.search(
        r"<script>\s*(\(function \(\) \{.*?\}\)\(\);)\s*</script>", head, re.S
    )
    theme_js = theme_snippet.group(1) if theme_snippet else ""

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>insider-intel — preview</title>",
        f"<script>{theme_js}</script>" if theme_js else "",
        f"<style>\n{_read_web('themes.css')}\n</style>",
        f"<style>\n{_read_web('styles.css')}\n</style>",
        "</head>",
        "<body>",
        body,
        _json_script("offline-articles", articles_json),
        _json_script("offline-itm", (PREVIEW / "data/itm.json").read_text()),
        _json_script("offline-manifest", json.dumps(manifest)),
        f"<script>{BOOTSTRAP}</script>",
        _inline(WEB / "config.js"),
        _inline(WEB / "ttp-packs.js"),
        _inline(PREVIEW / "offline-store.js"),
        _inline(WEB / "hunt-templates.js"),
        _inline(WEB / "board-share.js"),
        _inline(WEB / "app.js"),
        "</body>",
        "</html>",
    ]
    return "\n".join(p for p in parts if p), len(trimmed), len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist/insider-intel-demo.html")
    parser.add_argument("--articles", type=int, default=300)
    parser.add_argument("--blob-cap", type=int, default=800)
    args = parser.parse_args()

    text, kept, total = build(args.articles, args.blob_cap)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"articles: kept {kept}/{total} (blob cap {args.blob_cap})")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
