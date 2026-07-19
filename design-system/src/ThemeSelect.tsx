import type { DossierTheme } from "./DossierProvider";

export const DOSSIER_THEMES: DossierTheme[] = [
  "dossier",
  "midnight",
  "phosphor",
  "cnn-lite",
  "diablo",
];

const THEME_LABELS: Record<DossierTheme, string> = {
  dossier: "Dossier",
  midnight: "Midnight",
  phosphor: "Phosphor",
  "cnn-lite": "CNN Lite",
  diablo: "Diablo",
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
