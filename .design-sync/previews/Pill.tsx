import { Pill } from "insider-intel-dossier-ui";

export const ChannelFilters = () => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
    <Pill active>All channels</Pill>
    <Pill>News</Pill>
    <Pill>Cases</Pill>
    <Pill>Tips</Pill>
    <Pill>Social</Pill>
  </div>
);

export const InsiderTypes = () => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
    <Pill>Malicious</Pill>
    <Pill active>Negligent</Pill>
    <Pill>Unintentional</Pill>
  </div>
);
