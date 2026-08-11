import type { DossierTheme } from "./DossierProvider";

export const DOSSIER_THEMES: DossierTheme[] = [
  "dossier",
  "midnight",
  "phosphor",
  "cnn-lite",
  "diablo",
  "Dossier Sage",
  "Dossier Soft",
  "Dossier Fog",
  "Air Archive",
  "Cinder Archive",
  "Ice Archive",
  "Earth Archive",
  "Ultramarines",
  "Blood Ravens",
  "Black Templars",
  "Raven Guard",
  "Perplexity",
  "Linear",
  "Vercel",
  "ChatGPT",
  "Doom 3",
  "Diablo II",
  "StarCraft",
  "Brood War",
  "GoldenEye 64",
  "Warcraft III",
  "Bleach",
  "Ultima Online",
  "Evangelion",
  "EVA-01",
  "EVA-02",
  "EVA-03",
  "Cryostat",
  "Vermillion Court",
];

/**
 * Neutral display names, mirroring the site (web/app.js THEME_LABELS): theme
 * VALUES keep their internal ids, but user-facing labels never show media or
 * brand names. Unlisted ids fall back to a capitalized id.
 */
const THEME_LABELS: Partial<Record<DossierTheme, string>> = {
  "cnn-lite": "Wire Light",
  diablo: "Ember",
  "Diablo II": "Gilt Ember",
  "Doom 3": "Rust Terminal",
  Ultramarines: "Cobalt",
  "Blood Ravens": "Oxblood",
  "Black Templars": "Obsidian",
  "Raven Guard": "Graphite",
  StarCraft: "Void",
  "Brood War": "Ultraviolet",
  "GoldenEye 64": "Crimson Gold",
  "Warcraft III": "Banner Gold",
  Bleach: "Ivory Ink",
  "Ultima Online": "Parchment",
  Evangelion: "Violet",
  "EVA-01": "Violet Ops",
  "EVA-02": "Vermilion Ops",
  "EVA-03": "Onyx Ops",
  Perplexity: "Teal Console",
  Linear: "Indigo",
  Vercel: "Monochrome",
  ChatGPT: "Slate",
};

const themeLabel = (value: DossierTheme): string =>
  THEME_LABELS[value] ?? value.charAt(0).toUpperCase() + value.slice(1);

export interface ThemeSelectProps {
  /** Currently selected theme. */
  value: DossierTheme;
  /** Called with the newly picked theme; feed it back into DossierProvider. */
  onChange?: (theme: DossierTheme) => void;
  /** Subset of themes to offer (defaults to all four). */
  themes?: DossierTheme[];
}

/**
 * Labeled theme dropdown — the site's theme switcher. Controlled: hold the
 * theme in state, pass it to both DossierProvider and ThemeSelect, and update
 * it in onChange so the whole screen restyles live.
 */
export function ThemeSelect({ value, onChange, themes = DOSSIER_THEMES }: ThemeSelectProps) {
  return (
    <label className="ds-theme-select">
      <span className="ds-theme-select-label">Theme</span>
      <select
        value={value}
        onChange={(event) => onChange?.(event.target.value as DossierTheme)}
      >
        {themes.map((theme) => (
          <option key={theme} value={theme}>
            {themeLabel(theme)}
          </option>
        ))}
      </select>
    </label>
  );
}
