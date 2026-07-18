import type { ReactNode } from "react";

export interface PanelProps {
  /** Small-caps mono label rendered above the content (e.g. "EVIDENCE BOARD"). */
  title?: string;
  children?: ReactNode;
}

/**
 * Bordered content region on the panel surface, headed by a small-caps mono
 * label. The building block for rails and workbench sections (ITM INDEX,
 * EVIDENCE BOARD, HUNT REPORT).
 */
export function Panel({ title, children }: PanelProps) {
  return (
    <section className="ds-panel">
      {title ? <h2 className="ds-panel-title">{title}</h2> : null}
      {children}
    </section>
  );
}
