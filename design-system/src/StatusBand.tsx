export interface StatusBandProps {
  /** Left status text, e.g. "1,645 CASES · UPDATED 14 MIN AGO". */
  status?: string;
  /**
   * Ingestion-lane tally, e.g. "LANES 6/6" or "RSS ✓ CL ✓ SOCIAL ✓".
   * Rendered pre-formatted (whitespace preserved).
   */
  lanes?: string;
  /** Tint the lanes readout with the signal color (all lanes healthy). */
  lanesOk?: boolean;
  /** Right-edge UTC clock, e.g. "20:14:07 UTC". */
  clock?: string;
}

/**
 * The thin mono status band that sits between the Masthead and the content
 * grid: corpus status on the left, ingestion-lane health in the middle, UTC
 * clock pinned right. All text is supplied pre-formatted.
 */
export function StatusBand({ status, lanes, lanesOk, clock }: StatusBandProps) {
  return (
    <div className="ds-statusband">
      {status ? <span className="ds-statusband-status">{status}</span> : null}
      {lanes ? (
        <span
          className={
            lanesOk ? "ds-statusband-lanes ds-statusband-lanes--ok" : "ds-statusband-lanes"
          }
        >
          {lanes}
        </span>
      ) : null}
      {clock ? <span className="ds-statusband-clock">{clock}</span> : null}
    </div>
  );
}
