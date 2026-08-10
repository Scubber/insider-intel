import { Masthead } from "insider-intel-dossier-ui";

export const FullShellHeader = () => (
  <Masthead
    brand="insider-intel"
    corpusStats="1,645 CASES · 312 COURT-PROVEN · 41 TECHNIQUES OBSERVED"
    nav={[
      { label: "STREAM", active: true },
      { label: "MATRIX" },
      { label: "EVIDENCE" },
      { label: "WORKBENCH", badge: "[3]" },
      { label: "SETTINGS ⚙" },
    ]}
    liveStatus="LIVE"
    searchPlaceholder="SEARCH SCENARIOS — data theft, moonlighting, shared logins, sabotage…"
  />
);

export const CachedNoSearch = () => (
  <Masthead
    brand="insider-intel"
    corpusStats="REAL INSIDER CASES — WHAT ACTUALLY REACHES COURT"
    nav={[
      { label: "STREAM" },
      { label: "MATRIX" },
      { label: "EVIDENCE", active: true },
      { label: "WORKBENCH", badge: "[0]" },
    ]}
    liveStatus="CACHED"
  />
);
