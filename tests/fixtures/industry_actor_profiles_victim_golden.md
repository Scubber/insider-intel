# Actor profiles — industry: financial-services

Who the insiders were, by job function and employment state, in cases where the victim organization was in **financial-services**. Roles, never individuals.

## Basis funnel

| Stage | Rows |
|---|---:|
| JSONL lines | 7 |
| Deduped links (last line wins) | 7 |
| With forensics | 7 |
| Schema v3+ tier (industry was asked) | 6 |
| Verdict-true insider cases | 6 |
| With ≥1 extracted method | 6 |
| Cases after story merge | 5 |

Verdict-true rows below schema v3 (industry NOT ASKED, excluded from every industry count): **1**

Industry of verdict-true v3 rows:

| Industry | Rows |
|---|---:|
| financial-services | 4 |
| technology | 1 |
| unknown | 1 |
| defense | 0 |
| energy | 0 |
| healthcare | 0 |
| manufacturing | 0 |
| other | 0 |
| professional-services | 0 |
| public-sector | 0 |
| retail | 0 |

## Profiles — financial-services

Cases: **3** (from 4 rows after story merge). CASES counts one per story; ROWS counts every document behind it.

| Function | Employment state | Cases | Rows | Adjudicated/admitted | Alleged | Reported/unclear | Share |
|---|---|---:|---:|---:|---:|---:|---:|
| finance/accounting/ops | current (default fill) | 1 | 1 | 1 | 0 | 0 | n/a |
| finance/accounting/ops | former/fired | 1 | 1 | 1 | 0 | 0 | n/a |
| front-office/sales | current (default fill) | 1 | 2 | 0 | 1 | 0 | n/a |

## Unknown pool — industry: unknown (v3 contamination check)

Cases: **1** (from 1 rows after story merge). CASES counts one per story; ROWS counts every document behind it.

| Function | Employment state | Cases | Rows | Adjudicated/admitted | Alleged | Reported/unclear | Share |
|---|---|---:|---:|---:|---:|---:|---:|
| contractor/vendor | third-party | 1 | 1 | 1 | 0 | 0 | n/a |

## Motives by profile — financial-services

| Profile | Counts (cases) |
|---|---|
| finance/accounting/ops · current | MT005 ×1 |
| finance/accounting/ops · former/fired | MT005 ×1 |
| front-office/sales · current | MT005 ×1 |

## Legal posture by profile — financial-services

| Profile | Counts (cases) |
|---|---|
| finance/accounting/ops · current | conviction ×1 |
| finance/accounting/ops · former/fired | plea ×1 |
| front-office/sales · current | indictment ×1 |

## Reading rules

- INDUSTRY is the victim organization's sector, not the actor's employer: a contractor who hit a bank is a financial-services case.
- EMPLOYMENT STATE "current" is a default fill: the normalizer stamps it whenever a job function matched and the text carried no boundary language (former, resigned, contractor). Rows marked (default fill) measure the absence of that language, not tenure.
- The UNKNOWN POOL table (industry = unknown at v3) is the contamination check: the enricher could not read a sector from the source. If its profile mix mirrors the requested industry, the industry table is undercounting; if it differs, it is not.
- Pre-v3 rows are NOT ASKED, not unknown: their schema had no industry field. They never enter the unknown pool.
- Insider trading and embezzlement are literal COLLECTION LEXICON queries (CourtListener DEFAULT_QUERIES), so their motive and posture counts reflect what the corpus went looking for, not the base rate.
- ROLES, NEVER INDIVIDUALS: every cell is a count of cases; no name, title, link or quote is in this report.
- Percentages are suppressed below 10 cases (share shows as n/a).
- Case strength is the strongest method claim, capped by the document's legal posture: a complaint can never mint an adjudicated case.