import { useRef } from "react";

export interface MastheadNavItem {
  /** Uppercase mono section label, e.g. "STREAM", "MATRIX", "WORKBENCH". */
  label: string;
  /** The currently open section — renders as a solid-ink pill. */
  active?: boolean;
  /** Optional trailing badge, e.g. the workbench board count "[3]". */
  badge?: string;
  onSelect?: () => void;
}

export interface MastheadProps {
  /** Serif brand wordmark. Site value: "insider-intel". */
  brand?: string;
  /**
   * Mono corpus-stats line under the brand, e.g.
   * "1,645 CASES · 312 COURT-PROVEN · 41 TECHNIQUES OBSERVED".
   */
  corpusStats?: string;
  /** Section nav; exactly one item should be active. */
  nav?: MastheadNavItem[];
  /** Small right-edge freshness chip: "LIVE" or "CACHED". */
  liveStatus?: string;
  /**
   * When set, renders the single bordered query line under the brand row.
   * Enter submits (there is no SEARCH button in the redesign shell).
   */
  searchPlaceholder?: string;
  onSearch?: (query: string) => void;
}

/**
 * The site header: brand + corpus stats on the left, uppercase section nav
 * (active item = solid-ink pill) and a LIVE/CACHED chip on the right, with an
 * optional one-line search field below. Bottom-bordered; place it above
 * StatusBand.
 */
export function Masthead({
  brand = "insider-intel",
  corpusStats,
  nav = [],
  liveStatus,
  searchPlaceholder,
  onSearch,
}: MastheadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <header className="ds-masthead">
      <div className="ds-masthead-row">
        <div className="ds-masthead-brand-block">
          <h1 className="ds-masthead-brand">{brand}</h1>
          {corpusStats ? <p className="ds-masthead-corpus">{corpusStats}</p> : null}
        </div>
        {nav.length ? (
          <nav className="ds-masthead-nav">
            {nav.map((item) => (
              <button
                key={item.label}
                type="button"
                className={
                  item.active
                    ? "ds-masthead-nav-item ds-masthead-nav-item--active"
                    : "ds-masthead-nav-item"
                }
                onClick={item.onSelect}
              >
                {item.label}
                {item.badge ? ` ${item.badge}` : ""}
              </button>
            ))}
          </nav>
        ) : null}
        {liveStatus ? <span className="ds-masthead-live">{liveStatus}</span> : null}
      </div>
      {searchPlaceholder ? (
        <form
          className="ds-masthead-search"
          onSubmit={(event) => {
            event.preventDefault();
            onSearch?.(inputRef.current?.value ?? "");
          }}
        >
          <input
            ref={inputRef}
            type="search"
            placeholder={searchPlaceholder}
            aria-label="Search"
          />
        </form>
      ) : null}
    </header>
  );
}
