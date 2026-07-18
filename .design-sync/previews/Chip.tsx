import { Chip } from "insider-intel-dossier-ui";

export const OperatorTerms = () => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", maxWidth: "28rem" }}>
    <Chip>trade secret</Chip>
    <Chip>customer list</Chip>
    <Chip>non-compete</Chip>
    <Chip>rclone sync to personal cloud</Chip>
    <Chip>forward to Gmail</Chip>
  </div>
);

export const SignalTerms = () => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", maxWidth: "28rem" }}>
    <Chip signal>insider trading</Chip>
    <Chip signal>economic espionage</Chip>
    <Chip signal>data exfiltration</Chip>
  </div>
);
