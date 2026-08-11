import { StatusBand } from "insider-intel-dossier-ui";

export const AllLanesHealthy = () => (
  <StatusBand
    status="CORPUS 1,645 · UPDATED 14 MIN AGO"
    lanes="RSS ▮ FILINGS ▮ SOCIAL ▮ PUBS ▮"
    lanesOk
    clock="20:14:07 UTC"
  />
);

export const DegradedLanes = () => (
  <StatusBand
    status="CORPUS 1,645 · REFRESH RUNNING"
    lanes="RSS ▮ FILINGS ▯ SOCIAL ▮ PUBS ▮"
    clock="03:41:52 UTC"
  />
);
