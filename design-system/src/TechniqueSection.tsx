export interface TechniqueCase {
  /** Case name, e.g. "DictateMD, Inc. v. Ahmadi". */
  title: string;
  /** How this case performed the technique — short evidence bullets. */
  bullets: string[];
}

export interface TechniqueSectionProps {
  /** Insider Threat Matrix technique id, e.g. "IF016". */
  id: string;
  /** One-sentence technique description shown next to the id. */
  description: string;
  /** Cases observed under this technique, each with its evidence bullets. */
  cases?: TechniqueCase[];
}

/**
 * Per-technique hunt-report section: an accent-barred block headed by the ITM
 * id + description, listing each observed case with "how they did it"
 * bullets. Stack several to build a full hunt report.
 */
export function TechniqueSection({ id, description, cases = [] }: TechniqueSectionProps) {
  return (
    <article className="ds-technique">
      <p className="ds-technique-head">
        <span className="ds-technique-id">{id}</span>
        {description}
      </p>
      {cases.map((item) => (
        <TechniqueCaseBlock key={item.title} {...item} />
      ))}
    </article>
  );
}

function TechniqueCaseBlock({ title, bullets }: TechniqueCase) {
  return (
    <>
      <p className="ds-technique-case">{title}</p>
      {bullets.length ? (
        <ul className="ds-technique-bullets">
          {bullets.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
