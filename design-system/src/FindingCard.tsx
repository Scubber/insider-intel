export interface FindingCardProps {
  /** Headline claim, e.g. "Email wins insider cases — not the security stack". */
  title: string;
  /** The one number that carries the finding, e.g. "59%". */
  stat: string;
  /** What the number counts, in a reader's words. */
  statLabel?: string;
  /** One-paragraph consequence — why a decision maker should care. */
  takeaway?: string;
  /** Terse recommended actions. */
  recommendations?: string[];
  /** Dim footnote: method + honest caveats. */
  method?: string;
  /** Corner tag; defaults to the site's provenance label. */
  tag?: string;
}

/**
 * EVIDENCE-page finding card: a headline, one big number, one line of
 * consequence, terse actions, one dim method footnote. Signal-colored left
 * bar marks it as observed/alleged-class content (accent = court-proven).
 */
export function FindingCard({
  title,
  stat,
  statLabel,
  takeaway,
  recommendations = [],
  method,
  tag = "AI-ASSISTED",
}: FindingCardProps) {
  return (
    <article className="ds-finding">
      <div className="ds-finding-head">
        <span className="ds-finding-title">{title}</span>
        {tag ? <span className="ds-finding-tag">{tag}</span> : null}
      </div>
      <div className="ds-finding-stat">
        <span className="ds-finding-stat-num">{stat}</span>
        {statLabel ? <span className="ds-finding-stat-label">{statLabel}</span> : null}
      </div>
      {takeaway ? <p className="ds-finding-takeaway">{takeaway}</p> : null}
      {recommendations.length ? (
        <ul className="ds-finding-actions">
          {recommendations.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
      {method ? <p className="ds-finding-method">{method}</p> : null}
    </article>
  );
}
