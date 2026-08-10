import type { ReactNode } from "react";
import { FactList, type Fact } from "./FactList";

export type CaseStampVariant =
  | "malicious"
  | "negligent"
  | "unintentional"
  | "context";

export interface CaseStamp {
  /**
   * Uppercase stamp text. Insider classifications: "MALICIOUS", "NEGLIGENT",
   * "UNINTENTIONAL". Context rows say what the row is FOR, in ITM control
   * language: "DETECTION", "PREVENTION", "TRADECRAFT", "POLICY", "NEWS" (or
   * channel fallbacks like "LEGAL CONTEXT", "COMMUNITY", "REFERENCE").
   */
  label: string;
  /** Picks the stamp color; "context" renders muted with a dashed border. */
  variant: CaseStampVariant;
}

export interface CaseCardProps {
  /** File-tab stamp, e.g. "CASE 2026-0718-K4F" or "SOCIAL 2026-0301-9QZ". */
  tab: string;
  /** Case headline (serif display face). */
  title: string;
  /**
   * Mono provenance line under the title. Redesign format:
   * "SOURCE · FILED 2026-08-01 · 9D AGO · RETRIEVED 2026-08-05 · SIG 82",
   * optionally ending with the plain-language proof label — "CONFIRMED IN
   * COURT" (adjudicated/admitted), "ALLEGED" (a filing's theory), or
   * "REPORTED" (press/social only). Never conflate the three.
   */
  meta?: string;
  /**
   * In-flow classification stamp at the right edge of the meta line: the
   * insider type, or — for AI-adjudicated non-cases — a dashed context stamp
   * naming what the row is useful for.
   */
  stamp?: CaseStamp;
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
export function CaseCard({ tab, title, meta, stamp, note, facts, footer, actions }: CaseCardProps) {
  return (
    <article className="ds-case">
      <div className="ds-case-tab">{tab}</div>
      <div className="ds-case-body">
        <h3 className="ds-case-title">{title}</h3>
        {meta && stamp ? (
          <p className="ds-case-metarow">
            <span className="ds-case-meta">{meta}</span>
            <span className={`ds-case-stamp ds-case-stamp--${stamp.variant}`}>
              {stamp.label}
            </span>
          </p>
        ) : meta ? (
          <p className="ds-case-meta">{meta}</p>
        ) : null}
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
