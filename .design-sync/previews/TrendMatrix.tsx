import { TrendMatrix } from "insider-intel-dossier-ui";

/**
 * Techniques by filing year. Counts, not a curve: how many cases a year holds
 * reflects how deep that year's courts were swept, so a smooth line would
 * imply a measurement the corpus cannot support.
 */
export const TechniquesByYear = () => (
  <div style={{ maxWidth: "760px" }}>
    <TrendMatrix
      years={["2020", "2021", "2022", "2023", "2024", "2025"]}
      partialYear="2025"
      rows={[
        { id: "IF016", title: "Insider trading", counts: [27, 30, 27, 25, 32, 11] },
        { id: "IF002", title: "Data exfiltration", counts: [19, 23, 29, 22, 30, 9] },
        { id: "ME005", title: "Removable media", counts: [17, 22, 18, 23, 15, 6] },
        { id: "PR003", title: "Credential misuse", counts: [18, 17, 18, 20, 12, 4] },
        { id: "IF038", title: "Moonlighting", counts: [5, 8, 8, 10, 11, 3] },
      ]}
      note="Darker means more cases that year. 2025 is still filling, so it is never compared. Years below the reporting floor are hidden."
    />
  </div>
);
