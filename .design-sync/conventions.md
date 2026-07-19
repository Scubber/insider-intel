# Dossier UI conventions

Insider-intel's design language: a classified-file dossier on kraft paper.
Serif display (`Newsreader`) for case headlines, typewriter mono
(`Courier Prime`) for labels/ids/metadata, muted ink on paper panels, one
red accent. Dense, printed, evidentiary — never glossy SaaS.

## Wrapping and setup

Wrap every screen in exactly one `DossierProvider` — it stamps the theme's
CSS tokens (`data-theme`), the paper/atmosphere background, and the body
typeface. Without it, components sit on an unthemed background (token
fallbacks still apply, but the page loses its paper). Themes: `dossier`
(default), `midnight`, `phosphor`, `cnn-lite`, `diablo`, plus the Dark
Diablo Dossier pack: `Dossier Sage`, `Dossier Soft`, `Dossier Fog`,
`Air Archive`, `Cinder Archive`, `Ice Archive`, `Earth Archive`,
`Ultramarines`, `Perplexity`, `Linear`, `Vercel`, `ChatGPT`, `Doom 3`,
`Diablo II`, `StarCraft`, `Brood War`, `GoldenEye 64`, `Warcraft III`,
`Bleach`, `Ultima Online`, `Evangelion`, `EVA-01`, `EVA-02`, `EVA-03`,
`Cryostat`, `Vermillion Court`, `Blood Ravens`, `Black Templars`,
`Raven Guard` (theme names are case-sensitive and may contain spaces).

```tsx
import { DossierProvider, Panel, CaseCard, ItmChip, Chip, ActionButton } from "insider-intel-dossier-ui";

<DossierProvider theme="dossier">
  <Panel title="Case Stream">
    <CaseCard
      tab="CASE 2026-0718-K4F"
      title="DictateMD, Inc. v. Ahmadi"
      meta="COURTLISTENER RECAP · FILED 1D AGO · SIG 82"
      note="Departing engineer synced the customer database to a personal drive."
      facts={[{ label: "EXFIL", value: "Personal Dropbox" }]}
      footer={<><ItmChip id="IF016" /><Chip>trade secret</Chip></>}
      actions={<ActionButton>OPEN ↗</ActionButton>}
    />
  </Panel>
</DossierProvider>
```

## Styling idiom

Style via CSS custom properties, not utility classes — there is no class
vocabulary beyond the components' own. For your layout glue use inline
styles or small custom CSS referencing the tokens:
`--ink` `--muted` `--paper` `--panel` `--line` `--accent` `--accent-soft`
`--signal` `--signal-soft` `--hover` `--btn-fg` `--input-bg` `--side-bg`
`--focus` `--radius` `--font-display` `--font-body` `--font-mono`
`--headline-weight` `--headline-tracking` `--atmosphere`.
Idiom rules: labels/ids/meta are UPPERCASE `var(--font-mono)` at small
sizes with letter-spacing; headlines are `var(--font-display)`; borders are
1px `var(--line)`; the red `var(--accent)` is used sparingly (active
states, technique bars, primary buttons). Corners are near-square
(`var(--radius)` is 0–3px) — never rounded cards.

## Where the truth lives

Read `styles.css` (tokens for every theme + every `ds-*` component
class) before inventing styling; per-component APIs and examples are in
each component's doc card. Components: DossierProvider, Panel, CaseCard,
FactList, Chip, ItmChip, Pill, ActionButton, CopyButton, TechniqueSection,
ThemeSelect.

## Composition patterns

- Case lists = stacked `CaseCard`s (tab kinds: CASE / NEWS / SOCIAL; case
  numbers look like `2026-0718-K4F`).
- Structured case facts go in `FactList` (labels like ACTOR, METHODS,
  EXFIL, DETECTED VIA, OUTCOME).
- Hunt reports = a summary paragraph then stacked `TechniqueSection`s
  (ITM ids like IF016, ME007 — id chip + description + per-case bullets).
- Filters = `Pill` rows (one active); card footers = `Chip`/`ItmChip` left,
  `ActionButton`s right; workbench actions = `CopyButton` (one primary).
- Theme switching = `ThemeSelect`, controlled: hold the theme in state and
  pass it to both `DossierProvider` and `ThemeSelect` so the screen restyles
  live.
