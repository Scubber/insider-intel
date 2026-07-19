import type { ReactNode } from "react";

export type DossierTheme =
  | "dossier"
  | "midnight"
  | "phosphor"
  | "cnn-lite"
  | "diablo"
  | "Dossier Sage"
  | "Dossier Soft"
  | "Dossier Fog"
  | "Air Archive"
  | "Cinder Archive"
  | "Ice Archive"
  | "Earth Archive"
  | "Ultramarines"
  | "Perplexity"
  | "Linear"
  | "Vercel"
  | "ChatGPT"
  | "Doom 3"
  | "Diablo II"
  | "StarCraft"
  | "Brood War"
  | "GoldenEye 64"
  | "Warcraft III"
  | "Bleach"
  | "Ultima Online"
  | "Evangelion"
  | "EVA-01"
  | "EVA-02"
  | "EVA-03"
  | "Cryostat"
  | "Vermillion Court"
  | "Blood Ravens"
  | "Black Templars"
  | "Raven Guard";

export interface DossierProviderProps {
  /** Visual theme; "Dossier Sage" (light sage paper) is the brand default. */
  theme?: DossierTheme;
  children?: ReactNode;
}

/**
 * Root wrapper for every Dossier UI screen. Stamps the theme's design tokens
 * (data-theme attribute), the paper/atmosphere background, and the body
 * typeface. Components render unstyled surfaces without it — always wrap the
 * app (or the screen) in exactly one DossierProvider.
 */
export function DossierProvider({ theme = "Dossier Sage", children }: DossierProviderProps) {
  return (
    <div className="ds-root" data-theme={theme}>
      {children}
    </div>
  );
}
