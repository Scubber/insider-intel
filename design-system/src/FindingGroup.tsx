import type { ReactNode } from "react";

export interface FindingGroupProps {
  /** Group label, e.g. "WHO DID IT" — the question this group answers. */
  label: string;
  /** One-line purpose sub-line shown once the group is open. */
  blurb?: string;
  /** How many findings sit inside. */
  count: number;
  /**
   * The leading finding's stat, e.g. "46% of all cases name this group".
   * Shown on the header itself so a COLLAPSED group still teaches something
   * instead of reading as a bare label.
   */
  lead?: string;
  /** Open on first paint. Only the first group should be. */
  defaultOpen?: boolean;
  children?: ReactNode;
}

/**
 * A collapsible group of FindingCards, one per question the findings answer.
 *
 * Uses a native `<details>`: the disclosure state, keyboard handling and
 * find-in-page come from the element rather than a hand-rolled toggle, and no
 * `aria-expanded` is needed because the element carries it. The summary is a
 * 44px target and shares its chevron with every other expand on the page, so
 * a reader learns one gesture.
 *
 * Never render an empty group — a header advertising content that does not
 * exist is worse than no header.
 */
export function FindingGroup({
  label,
  blurb,
  count,
  lead,
  defaultOpen = false,
  children,
}: FindingGroupProps) {
  return (
    <details className="ds-fgroup" open={defaultOpen}>
      <summary className="ds-fgroup-summary">
        <span className="ds-fgroup-label">{label}</span>
        {lead ? <span className="ds-fgroup-lead">{lead}</span> : null}
        <span className="ds-fgroup-count">{count}</span>
      </summary>
      <div className="ds-fgroup-body">
        {blurb ? <p className="ds-fgroup-blurb">{blurb}</p> : null}
        {children}
      </div>
    </details>
  );
}
