import { ItmChip } from "insider-intel-dossier-ui";

export const Techniques = () => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
    <ItmChip id="IF016" title="Fraud" />
    <ItmChip id="IF016.004" title="Insider Trading" />
    <ItmChip id="ME007" title="Privileged Access" />
    <ItmChip id="MT003" title="Financial Gain" />
    <ItmChip id="AF001" title="Log Deletion" />
  </div>
);
