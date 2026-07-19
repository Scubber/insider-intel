# design-sync notes — insider-intel-dossier-ui

- The DS package lives at `design-system/` inside the (otherwise Python)
  insider-intel repo. It was purpose-built from the site's design language:
  tokens copied verbatim from `web/themes.css`, component CSS mirrors
  `web/styles.css` under `ds-` class names. If `web/styles.css` /
  `web/themes.css` change the look, port the values into
  `design-system/src/styles.css` and re-sync.
- Build: `cd design-system && npm install && npm run build` (tsup →
  `dist/index.js` + `index.css` + `index.d.ts`). Converter args:
  `--node-modules design-system/node_modules --entry design-system/dist/index.js`.
- Node deps for the converter live in `.ds-sync/` (npm; the repo itself has
  no root package.json — don't look for a lockfile-driven install).
- Playwright: sandbox chromium cache is build 1194 → `playwright@1.56.0`
  in `.ds-sync/` (the repo's Python playwright pins a different build).
- Webfonts (Newsreader, Courier Prime) load via a Google Fonts `@import`
  in the package CSS — validate reports `[FONT_REMOTE]`, which is expected;
  no font files ship.
- `cfg.provider` wraps every preview in a default `DossierProvider`; the
  DossierProvider preview nests its own themed providers inside it, so its
  theme cells show a thin kraft ring from the outer default wrapper —
  cosmetic, accepted.
- claude.ai/code remote session: `DesignSync(create_project)`'s permission
  prompt failed repeatedly with "permission stream closed" — if it recurs,
  have the user create the project in the claude.ai/design UI and re-adopt
  it by name.

## Known render warns

- (none — 10/10 render cleanly, no thin/identical warns on the final run)

## Re-sync risks

- The DS mirrors `web/` by hand; nothing detects drift between
  `web/styles.css` and `design-system/src/styles.css` — check when the site
  look changes.
- Preview content (case names/facts) is static sample data; it can lag the
  live corpus but that's cosmetic.
- Remote Google Fonts import means offline renders fall back to
  Georgia/Courier New.

- 2026-07 Dossier Theme Pack: 9 handoff themes (Dossier Sage/Soft/Fog, four
  Archives, Ultramarines, Perplexity) landed verbatim in both
  `web/themes.css` and `design-system/src/styles.css` — theme names contain
  spaces and are case-sensitive (`data-theme="Dossier Sage"`). IBM Plex Mono
  added to both Google Fonts imports (Ultramarines/Perplexity mono). The
  pack's density/layout/redaction "tweak controls" are NOT yet implemented
  in the site — follow-up work if wanted.
