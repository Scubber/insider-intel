import { FactList } from "insider-intel-dossier-ui";

export const CaseRecord = () => (
  <FactList
    items={[
      { label: "ACTOR", value: "Departing engineer" },
      { label: "ACCESS", value: "Production database admin" },
      { label: "METHODS", value: "Bulk download before resignation · personal cloud sync" },
      { label: "EXFIL", value: "Personal Dropbox · personal Gmail" },
      { label: "DETECTED VIA", value: "DLP alert on outbound mail" },
      { label: "OUTCOME", value: "Preliminary injunction granted" },
    ]}
  />
);
