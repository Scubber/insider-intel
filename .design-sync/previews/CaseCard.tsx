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
    meta="COURTLISTENER RECAP · FILED 1D AGO · SIG 82"
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
    meta="SECURITYWEEK · FILED 3D AGO · SIG 64"
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
