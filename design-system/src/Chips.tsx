import type { MouseEventHandler, ReactNode } from "react";

export interface ChipProps {
  /** Term text, e.g. "trade secret", "rclone sync to personal cloud". */
  children: ReactNode;
  /** Signal (orange/red) coloring instead of the default accent tint. */
  signal?: boolean;
}

/**
 * Soft-tinted mono term chip for operator search terms, matched aliases, and
 * case-record values. Wraps long terms instead of truncating.
 */
export function Chip({ children, signal = false }: ChipProps) {
  return <span className={signal ? "ds-chip ds-chip--signal" : "ds-chip"}>{children}</span>;
}

export interface ItmChipProps {
  /** Insider Threat Matrix technique id, e.g. "IF016" or "ME007". */
  id: string;
  /** Accessible/hover title, e.g. the technique name. */
  title?: string;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}

/**
 * Solid-ink Insider Threat Matrix technique chip — the highest-contrast chip
 * on a card; clicking it opens the technique dossier.
 */
export function ItmChip({ id, title, onClick }: ItmChipProps) {
  return (
    <button type="button" className="ds-itm-chip" title={title} onClick={onClick}>
      {id}
    </button>
  );
}

export interface PillProps {
  /** Filter label, e.g. "Cases", "All channels". */
  children: ReactNode;
  /** Selected state — accent-filled. */
  active?: boolean;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}

/**
 * Rounded uppercase filter pill for channel/theme filter rows. Exactly one
 * pill in a group is usually active.
 */
export function Pill({ children, active = false, onClick }: PillProps) {
  return (
    <button
      type="button"
      className={active ? "ds-pill ds-pill--active" : "ds-pill"}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
