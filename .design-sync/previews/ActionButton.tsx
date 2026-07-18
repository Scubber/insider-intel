import { ActionButton } from "insider-intel-dossier-ui";

export const CardActions = () => (
  <div style={{ display: "flex", gap: "0.85rem" }}>
    <ActionButton>+ FLAG</ActionButton>
    <ActionButton>OPEN ↗</ActionButton>
    <ActionButton>READ ⌄</ActionButton>
  </div>
);

export const EngagedActions = () => (
  <div style={{ display: "flex", gap: "0.85rem" }}>
    <ActionButton active>✓ FLAGGED</ActionButton>
    <ActionButton active>CLOSE ⌃</ActionButton>
    <ActionButton>READ FILING ⇩</ActionButton>
  </div>
);
