import type { ReactNode } from "react";

export type DossierTheme = "dossier" | "midnight" | "phosphor" | "cnn-lite" | "diablo";

export interface DossierProviderProps {
  /** Visual theme; "dossier" (kraft-paper classified file) is the brand default. */
  theme?: DossierTheme;
  children?: ReactNode;
}

/**
 * Root wrapper for every Dossier UI screen. Stamps the theme's design tokens
 * (data-theme attribute), the paper/atmosphere background, and the body
 * typeface. Components render unstyled surfaces without it — always wrap the
 * app (or the screen) in exactly one DossierProvider.
 */
export function DossierProvider({ theme = "dossier", children }: DossierProviderProps) {
  return (
    <div className="ds-root" data-theme={theme}>
      {children}
    </div>
  );
}
