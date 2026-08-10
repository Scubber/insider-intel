export interface EvidenceBarProps {
  /** Record class, e.g. "email logs / content". */
  label: string;
  /** Right-aligned count text, e.g. "545 · 66 proven". */
  count?: string;
  /** Share of all cases touching this record class, 0–1. */
  share: number;
  /** Share of court-proven cases, 0–1 — rendered as the accent overlay. */
  provenShare?: number;
}

const clamp = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;

/**
 * "Where the evidence lives" row: a label, a count, and a two-layer bar —
 * translucent signal for all cases, solid accent for the court-proven
 * subset. Never render without an EvidenceLegend nearby (color law:
 * accent = court-proven, signal = observed/alleged, always with a legend).
 */
export function EvidenceBar({ label, count, share, provenShare = 0 }: EvidenceBarProps) {
  return (
    <div className="ds-evrow">
      <div className="ds-evrow-top">
        <span className="ds-evrow-label">{label}</span>
        {count ? <span className="ds-evrow-count">{count}</span> : null}
      </div>
      <span className="ds-evbar">
        <span className="ds-evbar-all" style={{ width: clamp(share) }}>
          <span className="ds-evbar-adj" style={{ width: clamp(provenShare / (share || 1)) }} />
        </span>
      </span>
    </div>
  );
}

export interface EvidenceLegendProps {
  /** Label for the translucent layer. */
  allLabel?: string;
  /** Label for the solid accent layer. */
  provenLabel?: string;
}

/** The mandatory legend for EvidenceBar groups. */
export function EvidenceLegend({
  allLabel = "ALL CASES (mostly alleged)",
  provenLabel = "ADJUDICATED / ADMITTED — court-proven subset",
}: EvidenceLegendProps) {
  return (
    <p className="ds-evlegend">
      <span>
        <i className="ds-evlegend-all" />
        {allLabel}
      </span>
      <span>
        <i className="ds-evlegend-adj" />
        {provenLabel}
      </span>
    </p>
  );
}
