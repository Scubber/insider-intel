export interface TrendRow {
  /** Technique id, e.g. "IF016". */
  id: string;
  /** Spelled-out technique name; the id trails only when it adds something. */
  title?: string;
  /** One count per column, same order and length as `years`. */
  counts: number[];
}

export interface TrendMatrixProps {
  /** Column headings — filing years, oldest first. */
  years: string[];
  rows: TrendRow[];
  /**
   * The year still filling, if it is in `years`. Marked with an asterisk and
   * excluded from the change column — comparing into a partial year
   * manufactures a decline that never happened.
   */
  partialYear?: string;
  /** Footnote: what the shading means, what is hidden, what carries no date. */
  note?: string;
  onSelect?: (id: string) => void;
}

/**
 * Techniques by filing year, as a matrix of counts.
 *
 * Deliberately not a line chart. The corpus is query-driven, so how many cases
 * a year holds reflects how deep that year's courts were swept — a smooth
 * curve would imply a measurement of insider behavior over time that these
 * documents cannot support. A grid of counts reads as what it is.
 *
 * Colour: one sequential hue (`--signal`, light to dark) carries magnitude, so
 * there is no categorical palette to get wrong, and the count sits IN the cell,
 * so shading is a second encoding and never the only one. `--accent` stays
 * reserved for court-proven and is not used here.
 *
 * Caller supplies only years that clear its small-sample floor.
 */
export function TrendMatrix({ years, rows, partialYear, note, onSelect }: TrendMatrixProps) {
  const peak = Math.max(1, ...rows.flatMap((r) => r.counts));
  const complete = years.filter((y) => y !== partialYear);
  const later = complete[complete.length - 1];
  const earlier = complete[complete.length - 2];
  const at = (row: TrendRow, year: string) => row.counts[years.indexOf(year)] || 0;

  return (
    <div className="ds-trend">
      <div className="ds-trend-scroll">
        <table className="ds-trend-table">
          <thead>
            <tr>
              <th>TECHNIQUE</th>
              {years.map((y) => (
                <th key={y} className="ds-num">
                  {y === partialYear ? `${y}*` : y}
                </th>
              ))}
              <th className="ds-num">CHANGE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const delta = earlier ? at(row, later) - at(row, earlier) : null;
              return (
                <tr key={row.id}>
                  <td>
                    <button
                      type="button"
                      className="ds-trend-name"
                      onClick={() => onSelect?.(row.id)}
                    >
                      <span className="ds-trend-title">{row.title || row.id}</span>
                      {row.title && row.title !== row.id ? (
                        <span className="ds-trend-code">{row.id}</span>
                      ) : null}
                    </button>
                  </td>
                  {years.map((y) => {
                    const n = at(row, y);
                    return (
                      <td
                        key={y}
                        className={n ? "ds-num ds-trend-cell" : "ds-num ds-trend-zero"}
                        style={
                          n
                            ? {
                                background: `color-mix(in srgb, var(--signal) ${Math.round(
                                  8 + 34 * (n / peak),
                                )}%, transparent)`,
                              }
                            : undefined
                        }
                      >
                        {n || "—"}
                      </td>
                    );
                  })}
                  <td
                    className={
                      delta && delta > 0 ? "ds-num ds-trend-rise" : "ds-num ds-trend-flat"
                    }
                  >
                    {delta === null ? "—" : delta > 0 ? `+${delta}` : String(delta)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {note ? <p className="ds-trend-note">{note}</p> : null}
    </div>
  );
}
