import { CaseCard, DossierProvider, ItmChip } from "insider-intel-dossier-ui";

const sample = (
  <CaseCard
    tab="CASE 2026-0718-A21"
    title="United States v. Winner"
    meta="COURTLISTENER RECAP · SIG 77"
    note="Contractor printed a classified report and mailed it to a news outlet; the printer's microdots identified the source."
    footer={<ItmChip id="IF002" title="Exfiltration via Physical Medium" />}
  />
);

export const Dossier = () => <DossierProvider theme="dossier">{sample}</DossierProvider>;

export const Midnight = () => <DossierProvider theme="midnight">{sample}</DossierProvider>;

export const Phosphor = () => <DossierProvider theme="phosphor">{sample}</DossierProvider>;

export const CnnLite = () => <DossierProvider theme="cnn-lite">{sample}</DossierProvider>;
