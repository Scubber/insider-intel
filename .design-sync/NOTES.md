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

- `.ds-sync` converter deps now also need `ts-morph` (dts.mjs imports it);
  the 2026-08-10 run installed it alongside playwright/esbuild.
- 2026-08-10 re-sync: ported the post-July web surfaces as FindingCard,
  EvidenceBar(+EvidenceLegend), PatternCard (ds- classes copied verbatim
  from web/styles.css .evp-finding*/.evp-row*/.query-stack rules).
  EvidenceLegend ships floor-card (it renders inside EvidenceBar's
  authored preview); the CONTEXT case-stamp variant was deliberately
  skipped as low-value — port it if CaseCard grows a stamp slot.

- 2026-08-10 (2nd) re-sync: ported the redesign shell that PR #162 landed in
  `web/` — new Masthead (corpus stats, dark active nav pill, one-line search),
  StatusBand (lanes + UTC clock), SiteFooter (links/kbd/theme picker);
  CaseCard grew the `stamp` slot (insider-type hues + dashed context purpose
  stamps in ITM control language) and the redesign provenance meta format;
  `ds-chip` now matches the fidelity-pass term-chip (signal-soft bg);
  ThemeSelect labels went neutral (mirrors web/app.js THEME_LABELS —
  values keep internal ids, labels never show media/brand names).
- The converter needs the package self-linked into its own node_modules
  (`ln -sfn ../.. design-system/node_modules/insider-intel-dossier-ui`) so
  previews can import by package name — the link is not committed and must
  be recreated in a fresh checkout.

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

- 2026-07 Dark Diablo Dossier pack: ALL 29 handoff themes (the README's 9
  "deployed" plus the 20 extras — Diablo II, StarCraft/Brood War, EVA units,
  Space Marine chapters, Linear/Vercel/ChatGPT, etc.) landed verbatim in both
  `web/themes.css` and `design-system/src/styles.css`. Theme names contain
  spaces and are case-sensitive (`data-theme="Dossier Sage"`). Extra Google
  Fonts added to both imports: IBM Plex Mono, Space Grotesk, Space Mono,
  Cormorant Garamond, Xanh Mono. The pack's density/layout/redaction "tweak
  controls" are NOT yet implemented in the site — follow-up work if wanted.

- 2026-08-25 EVIDENCE redesign: added FindingGroup, TrendMatrix, and the
  TableControls trio (SortHeader / ExpandToggle / ExpandableRow); FindingCard
  gained `basis` and its `tag` default moved AI-ASSISTED -> DERIVED (findings
  are computed from the ledger now, nothing on that card is model-written).
  Authored previews exist for all of them and typecheck against dist/*.d.ts.
  `npm install && npm run build` in design-system/ both work in a Claude Code
  web sandbox.

  **The hosted re-sync did NOT run and could not.** The converter that emits
  `_ds_bundle.js` / `_ds_manifest.json` / `_preview/*` / the per-component
  `.jsx` `.d.ts` `.html` `.prompt.md` ships with the `design-sync` SKILL, which
  is not installed in a Claude Code web session (only design, dataviz and
  artifact-capabilities are bundled), and `.ds-sync/` is absent. The bundle is
  not hand-rollable: esbuild alone does not reproduce it, because the converter
  injects a `shim:react-shim` module mapping react onto `window.React` with its
  own jsx/jsxs implementations, plus the `__dsMainNs` namespace merge on the
  trailing `window.DossierUI=` line. Reproducing a compiler's output by
  inspection and pushing it to a design system other people's previews render
  from is not a safe trade, so it was left alone.

  To finish: run `/design-sync` from a session that has the skill. Everything
  it needs is committed — package source, previews, `.design-sync/config.json`
  (projectId 29c26c75-…). The live project is still on the pre-2026-08-25
  component set; the checked-in `design/redesign/_ds/` copy is older still
  (11 components vs the live 17) and is sync OUTPUT, so let the sync refresh
  it rather than hand-editing.

- **2026-08-29 manual re-sync DONE (no skill, DesignSync tool only).** A remote
  session that had the `DesignSync` transport tool but not the `/design-sync`
  skill completed the 2026-08-25 backlog by replicating the converter's output
  format, learned by reading the live project:
  - `_ds_bundle.js` = esbuild IIFE of `dist/index.js`, `globalName DossierUI`,
    react/jsx-runtime aliased to a `window.React` shim (source copied verbatim
    from the live bundle), `/* @ds-bundle: {...} */` JSON banner
    (components→sourcePath + sourceHashes), trailing
    `window.DossierUI=DossierUI.__dsMainNs?…` line. `sourceHashes` /
    `_ds_sync.json` hashes are **sha256[:12]** of the file bytes.
  - `_preview/<C>.js` = same pattern, global `__dsPreview`, package import
    aliased to `module.exports = window.DossierUI`.
  - `<C>.html` harnesses are one template (first line `<!-- @dsCard … -->`
    drives the pane's card index); `.jsx` files are two-line window re-export
    stubs; `_ds_needs_recompile` marks the project for the app's self-check.
  - Pushed: rebuilt bundle + CSS, quads and previews for FindingGroup,
    TrendMatrix, TableControls (SortHeader/ExpandToggle/ExpandableRow) and the
    refreshed FindingCard (basis/index/weight, tag default DERIVED). Validated
    first with a headless Chromium render check (5 new/changed + 6 regression
    stories, 0 bad) against the rebuilt bundle. Replica tooling lives in the
    session scratchpad only — a future real `/design-sync` run remains the
    canonical path and will treat any hash drift as ordinary changes.
  - `_ds_sync.json` and `_ds_manifest.json` were deliberately left stale (CLI
    bookkeeping / app-rebuilt); `design/redesign/_ds/` still awaits a real
    sync run.

