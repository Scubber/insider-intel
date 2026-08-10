import { PatternCard } from "insider-intel-dossier-ui";

export const SpotAndCounter = () => (
  <div style={{ maxWidth: "520px" }}>
    <PatternCard
      name="Pre-departure bulk collection"
      whoClass="DEPARTING · ANY ROLE"
      behavior="An employee who has decided to leave gathers far more material than their day-to-day work needs, in a short window, across repositories they rarely touch."
      detect={[
        "Volume of file access that departs sharply from the person's own baseline, especially in the weeks around a resignation.",
        "Access into repositories outside the person's normal working set.",
        "Large transfers to personal email, personal cloud storage, or removable media.",
      ]}
      prevent={[
        "Make resignation a trigger for access review, not just an HR event.",
        "Time-bound access to sensitive repositories to current project need.",
        "State clearly in offboarding what leaves with the employee and what does not.",
      ]}
      noise="Legitimate project handovers and backup habits produce similar spikes — corroborate before acting."
    />
  </div>
);
