# Web UI (local + future Pages)

Static Feedly-style reader + ITM theme filters + keyword workbench for **insider-intel**.

Articles are tagged with Insider Threat Matrix™ technique IDs. Theme chips
(Motive / Means / Preparation / Infringement / Anti-Forensics) call
`GET /articles?theme=…`. Footer includes required Forscie attribution.

## Local development

1. From `insider-intel/`:

```bash
pip install -e ".[dev]"
python -m apps.aggregator all
python -m apps.search serve
```

2. Serve or open this folder (API must allow the page origin via `CORS_ORIGINS`):

```bash
# example: Python static server on :5500 (matches default CORS)
python -m http.server 5500 --directory web
```

Open http://127.0.0.1:5500

Default API base: `http://127.0.0.1:8000` in [`config.js`](config.js).

## Hosting later (no secrets in this folder)

| Piece | Target |
|-------|--------|
| This `web/` folder | GitHub Pages @ `https://intel.thederpweb.com` (`td3.dev` redirects) |
| FastAPI (`apps.search serve`) | Google Cloud Run @ `https://api.intel.thederpweb.com` |
| `CORS_ORIGINS` | Include `https://intel.thederpweb.com`, `https://td3.dev`, `https://scubber.github.io` |
| `INSIDER_INTEL_API_BASE` | Cloud Run URL (set in `config.js` or inject before `app.js`) |

Never put API keys in this static UI.

## Themes

Switcher is in the UI (top-right). Themes live in [`themes.css`](themes.css) as `[data-theme="name"]` CSS variable blocks.

| Theme | Look |
|-------|------|
| `cnn-lite` (default) | White page, CNN red, Georgia headlines (lite.cnn.com) |
| `fox-news` | Red masthead stripe, Franklin/Arial condensed, uppercase brand |
| `feedly` | Cool gray reader, leaf green, Segoe/Helvetica UI sans |
| `yahoo-finance` | Yahoo purple bar, Helvetica Neue market-board sans |
| `voya` | Navy masthead + tangerine, Palatino brand / Segoe body |
| `dracula` | Classic palette; Cascadia/Consolas brand, Segoe body |
| `matrix` | Green-on-black terminal |
| `midnight` | Dark blue / cyan ops |
| `phosphor` | Amber CRT |
| `signal` | Original light OSINT look |
| `old-reddit` | Soft gray, orangered + blue links, Verdana (old.reddit.com) |

**Add a theme:** copy a block in `themes.css`, rename `data-theme`, tweak `--*` vars, add an `<option>` in `index.html`. Choice persists in `localStorage`.
