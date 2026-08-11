import type { ReactNode } from "react";
import type { DossierTheme } from "./DossierProvider";
import { ThemeSelect, DOSSIER_THEMES } from "./ThemeSelect";

export interface SiteFooterLink {
  /** Uppercase mono label, e.g. "METHODOLOGY & COLOPHON", "FEED.XML". */
  label: string;
  /** External/route href; omit for button-style links that only fire onSelect. */
  href?: string;
  onSelect?: () => void;
}

export interface SiteFooterProps {
  /** One-line site blurb shown above the links. */
  blurb?: string;
  /** Link row, separated with " · " automatically. */
  links?: SiteFooterLink[];
  /** Keyboard-shortcut hint line, e.g. "j/k move · x flag · ⏎ open · / search". */
  kbdHint?: string;
  /** Custom left-column content appended after the built-in rows. */
  children?: ReactNode;
  /** Current theme for the right-edge picker; omit theme to hide the picker. */
  theme?: DossierTheme;
  onThemeChange?: (theme: DossierTheme) => void;
  themes?: DossierTheme[];
}

/**
 * The redesign site footer: blurb + link row + keyboard hints stacked on the
 * left, theme picker on the right, top-bordered. This is where display
 * chrome lives — the redesign keeps shortcuts and the theme switcher out of
 * the header entirely.
 */
export function SiteFooter({
  blurb,
  links = [],
  kbdHint,
  children,
  theme,
  onThemeChange,
  themes = DOSSIER_THEMES,
}: SiteFooterProps) {
  return (
    <footer className="ds-sitefooter">
      <div className="ds-sitefooter-left">
        {blurb ? <span>{blurb}</span> : null}
        {links.length ? (
          <span className="ds-sitefooter-links">
            {links.map((link, index) => (
              <span key={link.label}>
                {index > 0 ? " · " : ""}
                {link.href ? (
                  <a href={link.href} target="_blank" rel="noopener">
                    {link.label}
                  </a>
                ) : (
                  <button
                    type="button"
                    className="ds-sitefooter-btn"
                    onClick={link.onSelect}
                  >
                    {link.label}
                  </button>
                )}
              </span>
            ))}
          </span>
        ) : null}
        {kbdHint ? <span className="ds-sitefooter-kbd">{kbdHint}</span> : null}
        {children}
      </div>
      {theme ? (
        <div className="ds-sitefooter-theme">
          <ThemeSelect value={theme} onChange={onThemeChange} themes={themes} />
        </div>
      ) : null}
    </footer>
  );
}
