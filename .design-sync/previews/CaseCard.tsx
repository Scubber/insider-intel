import {
  ActionButton,
  CaseCard,
  Chip,
  ItmChip,
} from "insider-intel-dossier-ui";

export const CourtCase = () => (
  <CaseCard
    tab="CASE 2026-0717-K4F"
    title="DictateMD, Inc. v. Ahmadi"
    meta="COURTLISTENER RECAP · FILED 2026-08-01 · 9D AGO · RETRIEVED 2026-08-05 · SIG 82 · CONFIRMED IN COURT"
    stamp={{ label: "MALICIOUS", variant: "malicious" }}
    note="Departing engineer accused of downloading the customer database and product schematics in the two weeks before resignation, syncing them to a personal cloud drive. Forensic review of the returned laptop surfaced the transfers."
    facts={[
      { label: "ACTOR", value: "Departing engineer" },
      { label: "METHODS", value: "Bulk download before resignation · personal cloud sync" },
      { label: "EXFIL", value: "Personal Dropbox" },
      { label: "DETECTED VIA", value: "Forensic review of returned laptop" },
    ]}
    footer={
      <>
        <ItmChip id="IF016" title="Fraud" />
        <Chip>trade secret</Chip>
        <Chip>customer list</Chip>
      </>
    }
    actions={
      <>
        <ActionButton active>✓ FLAGGED</ActionButton>
        <ActionButton>OPEN ↗</ActionButton>
        <ActionButton>READ ⌄</ActionButton>
      </>
    }
  />
);

export const NewsCase = () => (
  <CaseCard
    tab="NEWS 2026-0715-2QZ"
    title="Insider charged after exfiltrating source code to rival startup"
    meta="SECURITYWEEK · FILED 2026-08-07 · 3D AGO · SIG 64 · ALLEGED"
    note="Prosecutors say the developer cloned internal repositories to a personal laptop during his notice period and joined a competitor two weeks later."
    footer={
      <>
        <ItmChip id="ME024" title="Access to Source Code" />
        <Chip signal>source code</Chip>
      </>
    }
    actions={
      <>
        <ActionButton>+ FLAG</ActionButton>
        <ActionButton>OPEN ↗</ActionButton>
      </>
    }
  />
);

export const ContextRow = () => (
  <CaseCard
    tab="PUB 2026-0712-7HH"
    title="CISA guidance: detecting bulk cloud-sync exfiltration"
    meta="CISA · FILED 2026-07-12 · RETRIEVED 2026-07-14 · SIG 41"
    stamp={{ label: "DETECTION", variant: "context" }}
    note="Reference guidance, not a case — the enricher adjudicated it non-insider and stamped what it is useful FOR (ITM control language)."
    actions={<ActionButton>OPEN ↗</ActionButton>}
  />
);
