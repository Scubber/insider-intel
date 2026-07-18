import type { ReactNode } from "react";
import { FactList, type Fact } from "./FactList";

export interface CaseCardProps {
  /** File-tab stamp, e.g. "CASE 2026-0718-K4F" or "SOCIAL 2026-0301-9QZ". */
  tab: string;
  /** Case headline (serif display face). */
  title: string;
  /** Mono meta line under the title, e.g. "COURTLISTENER RECAP · FILED 3D AGO · SIG 82". */
  meta?: string;
  /** Analyst-note paragraph(s). */
  note?: string;
  /** Structured case facts (ACTOR / METHODS / EXFIL / OUTCOME…), rendered as a FactList. */
  facts?: Fact[];
  /** Left side of the footer — typically term Chips and an ItmChip. */
  footer?: ReactNode;
  /** Right-aligned footer actions — typically ActionButtons (+ FLAG, OPEN ↗, READ ⌄). */
  actions?: ReactNode;
}

/**
 * The signature surface: a case-file card with a solid-ink file-folder tab,
 * a paper body (headline, mono meta, analyst note, optional fact strip), and
 * a bordered footer for term chips and actions. One card = one case.
 */
export function CaseCard({ tab, title, meta, note, facts, footer, actions }: CaseCardProps) {
  return (
    <article className="ds-case">
      <div className="ds-case-tab">{tab}</div>
      <div className="ds-case-body">
        <h3 className="ds-case-title">{title}</h3>
        {meta ? <p className="ds-case-meta">{meta}</p> : null}
        {facts && facts.length ? <FactList items={facts} /> : null}
        {note ? <p className="ds-case-note">{note}</p> : null}
      </div>
      <div className="ds-case-footer">
        {footer}
        {actions ? <div className="ds-case-actions">{actions}</div> : null}
      </div>
    </article>
  );
}
