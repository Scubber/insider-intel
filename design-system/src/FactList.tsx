export interface Fact {
  /** Small-caps mono label, e.g. "ACTOR", "METHODS", "DETECTED VIA". */
  label: string;
  /** The fact text; join multiple values with " · ". */
  value: string;
}

export interface FactListProps {
  items: Fact[];
}

/**
 * Two-column definition grid for structured case facts — the CASE RECORD
 * strip (ACTOR / METHODS / EXFIL / DETECTED VIA / OUTCOME). Labels render in
 * small-caps mono, values in the body face.
 */
export function FactList({ items }: FactListProps) {
  return (
    <dl className="ds-facts">
      {items.map((fact) => (
        <FactRow key={fact.label + fact.value} {...fact} />
      ))}
    </dl>
  );
}

function FactRow({ label, value }: Fact) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
