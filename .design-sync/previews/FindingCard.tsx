import { FindingCard } from "insider-intel-dossier-ui";

export const Flagship = () => (
  <div style={{ maxWidth: "560px" }}>
    <FindingCard
      title="Email wins insider cases — not the security stack"
      stat="59%"
      statLabel="of court-proven insider cases turned on email evidence. Endpoint forensics: 22%. Central security logging (SIEM): 11%."
      takeaway="When an insider case actually gets proven, the decisive record is usually ordinary business email — not the specialized detection tools most budgets prioritize."
      recommendations={[
        "Fund email retention and legal-hold readiness like the case-winning asset it is.",
        "For each major record type, ask: can we produce it on demand, and how far back?",
        "Treat detection tools as detection, not evidence — courts need the second.",
      ]}
      method="Share of court-proven cases whose recorded evidence trail touches each record class, from model-extracted forensics on litigated cases."
    />
  </div>
);

export const Compact = () => (
  <div style={{ maxWidth: "560px" }}>
    <FindingCard
      title="Most of what you'll read about insider threats is unproven"
      stat="13%"
      statLabel="of insider cases with a described method are court-proven — 112 of 853"
      takeaway="Nearly nine in ten insider stories are a claim someone made in a lawsuit, not an established fact. Before a scary statistic moves your budget, ask which pile it came from."
    />
  </div>
);
