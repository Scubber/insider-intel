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
  "Blood Ravens",
  "Black Templars",
  "Raven Guard",
];

const THEME_LABELS: Record<DossierTheme, string> = {
  dossier: "Dossier",
  midnight: "Midnight",
  phosphor: "Phosphor",
  "cnn-lite": "CNN Lite",
  diablo: "Diablo",
  "Dossier Sage": "Dossier Sage",
  "Dossier Soft": "Dossier Soft",
  "Dossier Fog": "Dossier Fog",
  "Air Archive": "Air Archive",
  "Cinder Archive": "Cinder Archive",
  "Ice Archive": "Ice Archive",
  "Earth Archive": "Earth Archive",
  Ultramarines: "Ultramarines",
  Perplexity: "Perplexity",
  Linear: "Linear",
  Vercel: "Vercel",
  ChatGPT: "ChatGPT",
  "Doom 3": "Doom 3",
  "Diablo II": "Diablo II",
  StarCraft: "StarCraft",
  "Brood War": "Brood War",
  "GoldenEye 64": "GoldenEye 64",
  "Warcraft III": "Warcraft III",
  Bleach: "Bleach",
  "Ultima Online": "Ultima Online",
  Evangelion: "Evangelion",
  "EVA-01": "EVA-01",
  "EVA-02": "EVA-02",
  "EVA-03": "EVA-03",
  Cryostat: "Cryostat",
  "Vermillion Court": "Vermillion Court",
  "Blood Ravens": "Blood Ravens",
  "Black Templars": "Black Templars",
  "Raven Guard": "Raven Guard",
};

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
            {THEME_LABELS[theme] ?? theme}
          </option>
        ))}
      </select>
    </label>
  );
}
