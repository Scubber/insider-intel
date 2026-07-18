import { CopyButton } from "insider-intel-dossier-ui";

export const WorkbenchActions = () => (
  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
    <CopyButton primary>Copy hunt report</CopyButton>
    <CopyButton>Copy LLM query</CopyButton>
    <CopyButton>Extract TTPs</CopyButton>
  </div>
);
