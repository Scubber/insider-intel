import type { ReactNode } from "react";

export type SortDirection = "asc" | "desc";

export interface SortHeaderProps {
  label: string;
  /** True when this column is the active sort. */
  active?: boolean;
  direction?: SortDirection;
  /** What the column counts — becomes the hover tooltip. */
  hint?: string;
  onSort?: () => void;
}

/**
 * A sortable column header.
 *
 * Renders the `<button>` only; the caller owns the `<th>` and must set
 * `aria-sort` on it to "ascending" / "descending" / "none". The caret is dim
 * until the column is the active sort, so the table reads as sorted-by-one
 * rather than sortable-by-many.
 */
export function SortHeader({
  label,
  active = false,
  direction = "desc",
  hint,
  onSort,
}: SortHeaderProps) {
  return (
    <button
      type="button"
      className={active ? "ds-sort ds-sort-on" : "ds-sort"}
      data-tip={hint ? `${hint} — click to sort` : undefined}
      onClick={onSort}
    >
      <span>{label}</span>
      <span className="ds-sort-caret">{active && direction === "asc" ? "▲" : "▼"}</span>
    </button>
  );
}

export interface ExpandToggleProps {
  expanded: boolean;
  /** What opens — becomes the tooltip and the accessible label. */
  label: string;
  onToggle?: () => void;
}

/**
 * The in-place expand control for a table row.
 *
 * Deliberately separate from the row's primary action: on the EVIDENCE table
 * the technique NAME navigates away to a dossier while this chevron expands
 * detail in place, and one row carrying two destinations needs two
 * affordances. Same glyph and rotation as FindingGroup, so the page teaches
 * one disclosure gesture. 44px target.
 */
export function ExpandToggle({ expanded, label, onToggle }: ExpandToggleProps) {
  return (
    <button
      type="button"
      className="ds-expand"
      aria-expanded={expanded}
      aria-label={label}
      data-tip={label}
      onClick={onToggle}
    >
      {expanded ? "▾" : "▸"}
    </button>
  );
}

export interface ExpandableRowProps {
  /** Cells of the row itself. */
  children: ReactNode;
  /** Detail shown beneath when expanded. */
  detail?: ReactNode;
  expanded?: boolean;
  /** Column count the detail cell should span, including the chevron column. */
  span: number;
}

/**
 * A table row with in-place detail. Emits two `<tr>`s so the detail inherits
 * the table's column widths rather than floating in its own layout.
 */
export function ExpandableRow({ children, detail, expanded = false, span }: ExpandableRowProps) {
  return (
    <>
      <tr>{children}</tr>
      {expanded && detail ? (
        <tr className="ds-row-detail">
          <td />
          <td colSpan={Math.max(1, span - 1)}>{detail}</td>
        </tr>
      ) : null}
    </>
  );
}
