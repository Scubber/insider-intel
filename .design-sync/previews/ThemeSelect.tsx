import { useState } from "react";
import {
  CaseCard,
  DossierProvider,
  ItmChip,
  ThemeSelect,
  type DossierTheme,
} from "insider-intel-dossier-ui";

export const Selector = () => <ThemeSelect value="dossier" />;

export const LiveSwitch = () => {
  const [theme, setTheme] = useState<DossierTheme>("midnight");
  return (
    <DossierProvider theme={theme}>
      <div style={{ display: "grid", gap: "0.8rem" }}>
        <ThemeSelect value={theme} onChange={setTheme} />
        <CaseCard
          tab="CASE 2026-0718-A21"
          title="United States v. Winner"
          meta="COURTLISTENER RECAP · SIG 77"
          note="Contractor printed a classified report and mailed it to a news outlet; the printer's microdots identified the source."
          footer={<ItmChip id="IF002" title="Exfiltration via Physical Medium" />}
        />
      </div>
    </DossierProvider>
  );
};
