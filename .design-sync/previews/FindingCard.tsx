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

export const Derived = () => (
  <div style={{ maxWidth: "560px" }}>
    <FindingCard
      title="Some cases are proven by records no sensor of yours produces"
      stat="28%"
      statLabel="of proven cases turn on a record you cannot log"
      takeaway="29 of 105 proven cases rest on brokerage / trade records — held by the person's broker, not by your company. No amount of logging produces these records. Counsel, a regulator, or the person's own consent does."
      recommendations={[
        "Name who can obtain each of these today — counsel, compliance, or the investigator.",
        "Write the request path into the investigation playbook: who asks, under what authority, how long it takes.",
      ]}
      basis="BASED ON 105 CASES"
      method="Counts distinct proven cases whose evidence trail touches this record class. Securities cases are over-represented because the court queries search for them by name."
    />
  </div>
);
