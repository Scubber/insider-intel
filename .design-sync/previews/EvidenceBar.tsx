import { EvidenceBar, EvidenceLegend } from "insider-intel-dossier-ui";

export const WhereEvidenceLives = () => (
  <div style={{ maxWidth: "420px" }}>
    <EvidenceLegend />
    <EvidenceBar label="email logs / content" count="545 · 66 proven" share={1} provenShare={0.59} />
    <EvidenceBar label="system/file access logs" count="363 · 51 proven" share={0.67} provenShare={0.46} />
    <EvidenceBar label="workstation/device artifacts" count="176 · 25 proven" share={0.32} provenShare={0.22} />
    <EvidenceBar label="central audit trails (SIEM)" count="145 · 12 proven" share={0.27} provenShare={0.11} />
    <EvidenceBar label="removable-media (USB) logs" count="69 · 12 proven" share={0.13} provenShare={0.11} />
  </div>
);
