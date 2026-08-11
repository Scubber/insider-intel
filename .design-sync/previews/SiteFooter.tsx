import { useState } from "react";
import {
  DossierProvider,
  SiteFooter,
  type DossierTheme,
} from "insider-intel-dossier-ui";

export const FullFooter = () => {
  const [theme, setTheme] = useState<DossierTheme>("dossier");
  return (
    <DossierProvider theme={theme}>
      <SiteFooter
        blurb="insider-intel — evidence-based insider-threat research, built from what actually reaches court."
        links={[
          { label: "METHODOLOGY & COLOPHON" },
          { label: "ITM™ © FORSCIE LTD — NOT AFFILIATED", href: "https://insiderthreatmatrix.org/" },
          { label: "FEED.XML", href: "#" },
          { label: "SETTINGS" },
        ]}
        kbdHint="j/k move · x flag · ⏎ open · / search"
        theme={theme}
        onThemeChange={setTheme}
      />
    </DossierProvider>
  );
};

export const MinimalFooter = () => (
  <SiteFooter
    blurb="insider-intel — corpus-wide forensic aggregation."
    links={[{ label: "FEED.XML", href: "#" }]}
  />
);
