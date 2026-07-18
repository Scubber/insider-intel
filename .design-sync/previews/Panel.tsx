import { Chip, CopyButton, Panel, Pill } from "insider-intel-dossier-ui";

export const EvidenceBoard = () => (
  <Panel title="Evidence Board">
    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
      <Chip>DictateMD, Inc. v. Ahmadi</Chip>
      <Chip>SEC v. Chammout</Chip>
      <Chip signal>United States v. Winner</Chip>
    </div>
  </Panel>
);

export const HuntReport = () => (
  <Panel title="Hunt Report">
    <p style={{ margin: "0 0 0.7rem", fontSize: "0.9rem" }}>
      4 board cases show 7 ITM techniques: IF016, IF016.004, ME007, ME024, MT003, IF012, MT021.
    </p>
    <div style={{ display: "flex", gap: "0.5rem" }}>
      <CopyButton primary>Copy hunt report</CopyButton>
      <CopyButton>Copy LLM query</CopyButton>
    </div>
  </Panel>
);

export const FilterPanel = () => (
  <Panel title="Channel">
    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
      <Pill active>All channels</Pill>
      <Pill>News</Pill>
      <Pill>Cases</Pill>
      <Pill>Social</Pill>
    </div>
  </Panel>
);
