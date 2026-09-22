# Methodology — Voya Financial insider threat profiles

**Version:** Phase 1 draft (2026-09-22). The taxonomy and rubric are
fixed here before tagging so scores cannot be tuned to a preferred
answer. Phase 2 adds the tagged case counts and the sampling record;
Phase 3 adds the limitations found while tagging.

## 1. Source and scope

- **Source:** the insider-intel corpus only —
  `gs://insider-intel-502413-corpus/processed/articles.jsonl`, read
  last-line-wins per link (the API's own rule). No Voya-internal data, no
  web research.
- **Case definition:** a row whose projected enrichment says
  `is_insider_case=true` on the current schema tier (v3) with at least one
  extracted method, after story merge. This is the EVIDENCE page's own
  verdict gate, so the study reconciles with the site.
- **Ranking base:** US court records (`country=US`) whose victim
  `industry` is `financial-services`. Non-US cases (India, Canada) are
  tagged and reported in `annex-non-us.md`; they enter a profile's
  supporting count only when the pattern is jurisdiction-independent
  (e.g. fund diversion through an approval workflow) and the annex says so
  per case.
- **Dateline:** every number carries the refresh date it was read from.
  The corpus refreshes daily; the report is a dated snapshot, and the live
  figures are on the EVIDENCE page.

## 2. Tagging taxonomy

The full field list with values and derivation is in
`phase1-inventory.md` §2 and is implemented in `tools/tag_cases.py`.
Summary of layers:

| Layer | Tags | How assigned |
|---|---|---|
| Derived (mechanical, reproducible) | function, employment_status, country, industry, actor_employer_sector, strength (posture-capped), ITM technique ids, motive ids, evidence record classes, observable channels, exfil channels, filing year | `tag_cases.py`, using the corpus' own normalizers (`shared/utils/evidence.py`) |
| Read (from `ai_summary`, methods, quotes) | fs_role, access_level, insider_type (CERT), primary motive, target assets, ATT&CK ids, non-technical method, precursors, detected_by, time_to_detection, loss band, records band, regulatory action, fs_subsector | analyst pass over the tagged JSONL; every read tag cites the field or quote it came from |
| Body (needs the document text) | tenure, dollar figures not in `quantity`, detection details absent from `detection` | only where the read layer is silent and the body is available |

Rules:

- `unknown` means the record is silent. Nothing is inferred from a name,
  a title alone, or a court's location.
- Employment state "current" is the normalizer's default fill (no departure
  language in the record). It is reported with that label and never
  headlined as a tenure finding.
- Adjudicated/admitted, alleged and reported are never pooled. A
  complaint's theory is alleged even when the enricher stamped a method
  "adjudicated"; the document posture caps strength.
- Roles, never individuals: the deliverables name cases by docket title
  and link, and describe actors by function. No defendant is named in
  prose.

## 3. Profile construction

A profile is a recurring combination of **actor (function × employment
state × access) + motive + target + method**. Clustering is done on the
tagged set, not on the technique table alone:

1. Group cases by primary insider type and primary target.
2. Within each group, split by actor function/state and by the dominant
   method family (ITM means/preparation ids, exfil channel).
3. Merge splits that share actor, motive, target and method and differ
   only in outcome; keep splits apart when the control that would stop
   them differs (that is the point of a profile for an ITM program).
4. A case may support at most two profiles (primary and secondary
   mapping, recorded in `evidence.csv` with a `mapping` column).
5. Candidates: 12–15. Cut to ≤10 by score. Candidates with fewer than 5
   supporting US cases are reported but flagged low-confidence; distinct
   archetypes are never merged to reach ten.

## 4. Scoring rubric (0–5 per axis, weighted)

| Axis | Weight | 0 | 3 | 5 |
|---|---:|---|---|---|
| Business-model relevance to Voya | 3 | No Voya exposure named in the task anchors is touched | One anchor exposure (e.g. advisor relationships) | Touches participant PII/balances or fund movement in retirement/insurance/asset-management operations, or a privileged admin/contractor seat, with a matching regulatory hook (SEC/FINRA/DOL/state insurance) |
| Corpus frequency among financial-services cases | 2 | <3 supporting US cases | 5–9 | ≥15, or ≥10 with ≥2 proven |
| Impact severity | 2 | No stated loss or records; posture below indictment | Median stated loss $100k–$1M or 1k–100k records, or one regulatory action | Median loss >$1M or >100k records, or repeated SEC/FINRA/DOL action |
| Detectability gap | 2 | Mostly caught by internal alert within 30 days | Mixed; detection unknown for most cases | Mostly detected late (>180d) or by an external party (regulator, competitor, customer, law enforcement) |
| Trend 2021–2026 | 1 | Falling share of financial-services cases by filing year | Flat | Rising share across ≥2 consecutive years above the small-n floor; sweep-depth caveat applied |

Score = 3·relevance + 2·frequency + 2·severity + 2·gap + 1·trend, max 50.
Ties break on proven-case count, then on relevance. The full table,
including cut candidates and the reason each was cut, is published in
`report.md`.

Relevance is scored against the task's anchors: participant PII and
account balances; plan-sponsor and advisor relationships; call-center and
operations staff with account-servicing access; registered reps/advisors;
trading and portfolio data; claims processing; TPAs and offshore/contractor
staff; privileged IT/cloud admins; regulators SEC/FINRA, DOL/ERISA, state
insurance, NYDFS Part 500.

## 5. Sampling

None planned. The US financial-services base is 78 cases on 2026-09-22
(a census). If the base grows past ~300 before Phase 2 runs, tag all
proven cases plus a stratified random sample by function × technique,
seeded and recorded here.

## 6. Verification

Every case cited in `report.md` is checked against its stored corpus
record (link, verdict, posture, the quoted method) before it is kept; a
case whose record cannot be located is dropped, not paraphrased.
Verification is against the corpus record, not CourtListener, unless the
session can reach the docket.

## 7. Bias and limitations (stated now, extended in Phase 3)

- **Prosecuted-case corpus.** Only insiders who were caught and litigated
  are present. The corpus over-represents charged fraud and data/IP theft
  and under-represents sabotage, negligence, and cases settled or handled
  internally. Nothing here is a real-world base rate.
- **Collection lexicon.** "insider trading", "trade secret", "former
  employee", "terminate", "moonlighting" and the Voya company watchlist are
  literal CourtListener queries; their volumes are partly manufactured.
  Within-corpus rankings stand; absolute prevalence does not.
- **Civil-complaint weight.** 68 of the 78 US financial-services cases are
  alleged (complaints, TROs in client-solicitation suits). The proven set
  is 10. Severity and detectability judgements rest mostly on one side's
  pleading.
- **Model-read fields.** Roles, methods, detection and observables are
  read from filings by an LLM (Nemotron 3 Super for 1,200 of 1,248
  cases); 64% of evidence quotes verify verbatim. Sizes are directional.
- **Sweep depth.** Filing-year counts reflect how deep the CourtListener
  sweep reached each year; the trend axis carries the smallest weight for
  that reason.
- **Sub-sector blindness.** No stored field distinguishes retirement
  recordkeeping from banking; the sub-sector tag is hand-read and will be
  reported with its unknown rate.
- **Voya-named rows are a collection artifact** (watchlist) and are never
  treated as evidence about Voya.
