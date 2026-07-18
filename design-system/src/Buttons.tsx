import type { MouseEventHandler, ReactNode } from "react";

export interface ActionButtonProps {
  /** Uppercase mono label, e.g. "+ FLAG", "OPEN ↗", "READ ⌄". */
  children: ReactNode;
  /** Highlighted/engaged state (e.g. "✓ FLAGGED"). */
  active?: boolean;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}

/**
 * Borderless mono text action for case-card footers (+ FLAG / OPEN ↗ /
 * READ ⌄). Muted by default, accent on hover or when active.
 */
export function ActionButton({ children, active = false, onClick }: ActionButtonProps) {
  return (
    <button
      type="button"
      className={active ? "ds-action-btn ds-action-btn--active" : "ds-action-btn"}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export interface CopyButtonProps {
  /** Button label, e.g. "Copy hunt report". */
  children: ReactNode;
  /** Accent-filled primary emphasis. */
  primary?: boolean;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}

/**
 * Bordered mono button for workbench actions (copy report, export, extract).
 * Use primary for the single most important action in a group.
 */
export function CopyButton({ children, primary = false, onClick }: CopyButtonProps) {
  return (
    <button
      type="button"
      className={primary ? "ds-copy-btn ds-copy-btn--primary" : "ds-copy-btn"}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
