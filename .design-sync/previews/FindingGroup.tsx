import { FindingCard, FindingGroup } from "insider-intel-dossier-ui";

/**
 * The EVIDENCE findings section: one group per question the findings answer,
 * the first open and the rest collapsed. A collapsed header still carries its
 * leading stat, so scanning the shut state still tells the reader something.
 */
export const GroupedFindings = () => (
  <div style={{ maxWidth: "620px" }}>
    <FindingGroup
      label="WHO DID IT"
      blurb="Which kind of person these cases name"
      count={2}
      lead="57% of all cases name this group"
      defaultOpen
    >
      <FindingCard
        title="Executive/officer is the group these cases name most"
        stat="57%"
        statLabel="of all cases name this group"
        takeaway="343 of 600 cases name executive/officer — well clear of the next group, technical at 172. 59 of those are proven in court."
        recommendations={[
          "Set escalation triggers by behavior, not seniority — and set them before there is a case.",
          "Give senior-level concerns a path around the usual chain: the audit committee, or outside counsel.",
        ]}
        basis="BASED ON 600 CASES"
        method="Roles are read out of filings by a model, not hand-audited, so treat the size as directional."
      />
    </FindingGroup>

    <FindingGroup
      label="WHAT PROVES A CASE"
      blurb="Which records carry proven cases"
      count={2}
      lead="28% of proven cases turn on a record you cannot log"
    />

    <FindingGroup
      label="WHAT'S CHANGING"
      blurb="Which tactics move year to year"
      count={1}
      lead="+12 more cases in 2024 than 2023"
    />
  </div>
);
