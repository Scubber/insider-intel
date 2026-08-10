# Web UI (local + GitHub Pages)

Static article reader + ITM theme filters + keyword workbench for **insider-intel**.

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

## Hosting (no secrets in this folder)

| Piece | Target |
|-------|--------|
| This `web/` folder | GitHub Pages @ `https://intel.thederpweb.com` |
| FastAPI (`apps.search serve`) | Google Cloud Run @ `https://api.intel.thederpweb.com` |
| `CORS_ORIGINS` | Include `https://intel.thederpweb.com`, `https://scubber.github.io` |
| `INSIDER_INTEL_API_BASE` | Cloud Run URL (set in `config.js` or inject before `app.js`) |

Never put API keys in this static UI.

## Themes

Switcher lives in **Settings → Appearance**. Themes are `[data-theme="…"]`
CSS variable packs in [`themes.css`](themes.css); the picker options live in
[`index.html`](index.html) and the allow-list is `THEMES` in [`app.js`](app.js).

**Default:** `Dossier Sage` (Newsreader + Courier Prime; also stamped in the
`<head>` pre-paint script so first paint matches). Choice persists in
`localStorage` under `insider-intel-theme`.

| Theme family | Examples |
|--------------|----------|
| Dossier (default pack) | `Dossier Sage`, `Dossier Soft`, `Dossier Fog`, `dossier` |
| Archives | `Air Archive`, `Cinder Archive`, `Ice Archive`, `Earth Archive` |
| Classic backups | `cnn-lite` (Light), `midnight` (Dark), `phosphor` (Terminal), `diablo` |
| Pack skins | Warhammer / AI-product / game / anime skins in the same picker |

**Add a theme:** copy a `[data-theme]` block in `themes.css`, add an
`<option>` in `index.html`, and add the name to the `THEMES` set in `app.js`.
