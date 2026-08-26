import { FindingCard } from "insider-intel-dossier-ui";

/**
 * A lead finding — the full anatomy. Headline carries its own subject AND its
 * own magnitude, so it still reads true with the group header deleted.
 * Numbered, because numbering is what separates a report from a feed.
 */
export const Lead = () => (
  <div style={{ maxWidth: "560px" }}>
    <FindingCard
      index={1}
      weight="lead"
      title="Executive/officer is named in 52% of these cases"
      stat="52%"
      statLabel="of all cases name this group"
      takeaway="400 of 766 cases name executive/officer; the next group, manager, appears in 81. 18 of those are proven in court."
      recommendations={[
        "Apply the same escalation triggers to executive/officer that everyone else gets, and set them before there is a case.",
        "Give concerns about this group a reporting path that does not run through it — the audit committee, or outside counsel.",
      ]}
      basis="BASED ON 766 CASES"
      method="Counted against every case with methods, which is the base the bars below use."
    />
  </div>
);

/**
 * A supporting finding — states its claim and its evidence and stops.
 *
 * The reason this variant exists: five cards at identical visual weight read as
 * generated filler however good each one is. Recommendations still ship in the
 * payload and still print in the CLI report; the page de-emphasises them.
 */
export const Supporting = () => (
  <div style={{ maxWidth: "560px" }}>
    <FindingCard
      index={2}
      weight="supporting"
      title="Former/fired accounts for 16% of cases but 33% of the proven ones"
      stat="33%"
      statLabel="of proven cases involve this group"
      takeaway="21 of the 64 proven cases involve former/fired, against 122 of 766 overall. Something about these cases survives to a ruling that the others do not."
      basis="BASED ON 64 CASES"
    />
  </div>
);
