export interface PatternCardProps {
  /** Pattern name, e.g. "Departing-employee bulk download". */
  name: string;
  /** Who exhibits it, e.g. "DEPARTING · TECHNICAL". */
  whoClass?: string;
  /** One-sentence description of the behavior. */
  behavior?: string;
  /** Plain-language ways to spot it (tool-agnostic — never query syntax). */
  detect?: string[];
  /** Plain-language ways to counter it (controls, process, people). */
  prevent?: string[];
  /** Legitimate look-alikes that generate noise. */
  noise?: string;
  /** Collapsed/expanded state; matches the site's default-open cards. */
  open?: boolean;
}

/**
 * Dossier hunt-pattern card: a synthesized, tool-agnostic detect/counter
 * pattern ("How to spot it / How to counter it"). Methods are plain
 * language spanning telemetry, process, and people — never SIEM query
 * syntax, product names, or case-specific literals.
 */
export function PatternCard({
  name,
  whoClass,
  behavior,
  detect = [],
  prevent = [],
  noise,
  open = true,
}: PatternCardProps) {
  return (
    <details className="ds-pattern" open={open}>
      <summary className="ds-pattern-summary">
        <span>{name}</span>
        {whoClass ? <span className="ds-pattern-who">{whoClass}</span> : null}
      </summary>
      {behavior ? <p className="ds-pattern-hint">{behavior}</p> : null}
      {detect.length ? (
        <>
          <p className="ds-pattern-hint">How to spot it</p>
          <ul className="ds-pattern-methods">
            {detect.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </>
      ) : null}
      {prevent.length ? (
        <>
          <p className="ds-pattern-hint">How to counter it</p>
          <ul className="ds-pattern-methods">
            {prevent.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </>
      ) : null}
      {noise ? <p className="ds-pattern-hint">Legitimate look-alikes: {noise}</p> : null}
    </details>
  );
}
