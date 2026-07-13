"""Bundle the web UI into one self-contained HTML file (demo mode).

Produces a single .html that runs the full UI offline against an embedded,
trimmed copy of the web/demo snapshot — handy for demos: open the file
directly in a browser, or host it anywhere as one asset.

Run from insider-intel/:
  python -m scripts.export_demo_bundle [--out dist/insider-intel-demo.html]
                                       [--articles 300] [--blob-cap 800]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

BOOTSTRAP = """
(function () {
  // Hosts (e.g. artifact viewers) may stamp data-theme="dark"/"light" on the
  // root element; map those onto the app's own theme names.
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
  window.INSIDER_INTEL_DEMO = true; // single-file bundle: demo snapshot only
  var origFetch = window.fetch.bind(window);
  function embedded(id) {
    return document.getElementById(id).textContent;
  }
  window.fetch = function (input, init) {
    var url = String(input && input.url ? input.url : input);
    if (url.indexOf("/demo/") !== -1) {
      var id = /articles\\.json(\\?|$)/.test(url)
        ? "demo-articles"
        : /itm\\.json(\\?|$)/.test(url)
          ? "demo-itm"
          : /manifest\\.json(\\?|$)/.test(url)
            ? "demo-manifest"
            : null;
      if (id) {
        return Promise.resolve(
          new Response(embedded(id), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
    }
    return origFetch(input, init);
  };
})();
"""


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _json_script(el_id: str, text: str) -> str:
    # Keep </script> (and comment openers) from terminating the block; both
    # replacements stay valid inside JSON strings.
    safe = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    return f'<script id="{el_id}" type="application/json">{safe}</script>'


def _inline_js(name: str) -> str:
    return f"<script>\n{_read(name)}\n</script>"


def build(article_cap: int, blob_cap: int) -> tuple[str, int, int]:
    articles_data = json.loads(_read("demo/articles.json"))
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

    manifest = json.loads(_read("demo/manifest.json"))
    manifest["article_count"] = len(trimmed)
    manifest["note"] = "Trimmed single-file demo bundle — not live ingest."

    html = _read("index.html")
    head, rest = html.split("<body>", 1)
    body = rest.rsplit("</body>", 1)[0]
    body = re.sub(r'\s*<script src="\./[^"]+"></script>', "", body)

    theme_snippet = re.search(r"<script>\s*(\(function \(\) \{.*?\}\)\(\);)\s*</script>", head, re.S)
    theme_js = theme_snippet.group(1) if theme_snippet else ""

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>insider-intel — demo</title>",
        f"<script>{theme_js}</script>" if theme_js else "",
        f"<style>\n{_read('themes.css')}\n</style>",
        f"<style>\n{_read('styles.css')}\n</style>",
        "</head>",
        "<body>",
        body,
        _json_script("demo-articles", articles_json),
        _json_script("demo-itm", _read("demo/itm.json")),
        _json_script("demo-manifest", json.dumps(manifest)),
        f"<script>{BOOTSTRAP}</script>",
        _inline_js("config.js"),
        _inline_js("demo-store.js"),
        _inline_js("hunt-templates.js"),
        _inline_js("board-share.js"),
        _inline_js("app.js"),
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
