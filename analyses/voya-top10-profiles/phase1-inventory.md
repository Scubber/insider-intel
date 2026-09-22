# Phase 1 — Corpus inventory, tagging taxonomy, and counts by sector

**Status:** Phase 1 delivered for review. Phases 2–3 are blocked on
per-case corpus access from this session (see §6). **AS OF:** the
2026-09-22 08:00Z refresh of `gs://insider-intel-502413-corpus/processed/articles.jsonl`
(9,512 rows), read through the repo's read-only Actions diagnostics on
2026-09-22 (run ids in `inputs/2026-09-22-corpus-diagnostics.md`).

**Bottom line.** The corpus holds 1,248 verdict-true insider cases with
extracted methods, 98 of them proven. Only **190** of those name a
financial-services victim, and only **78** of those are US court records
(10 proven). India contributes 109 financial-services cases (5 proven) and
belongs in the comparative annex per the task rules. So the ranking base
for Voya is **78 US cases**: a census, not a sample, and small enough that
several archetypes will fall under the 5-case confidence floor. Expect 6–8
well-supported profiles plus a listed set of weak candidates, not a padded
ten.

---

## 1. Corpus inventory

### 1.1 Layout

| Store | Path | What it holds |
|---|---|---|
| Processed corpus | `gs://insider-intel-502413-corpus/processed/articles.jsonl` | One JSON object per document (`ProcessedArticle`). Appended mid-cycle; **last line wins per `link`** (`shared/utils/evidence.py::collapse_rows_by_link`). This is the only table. |
| Raw ingest | `gs://…/raw/articles.jsonl` | `RawArticle` rows before processing. Not needed for this study. |
| Per-cycle state | `gs://…/state/` | `lane_health.json`, `technique_hunts.json`, `technique_seeds.json`, `field_backfill_targets.json`. Not case data. |
| Exports | `gs://…/export/` | Outputs of the diagnostics (evidence ledgers, industry profiles, peer-set study — the last is PRIVATE by operator rule). |
| Local copy in this checkout | `preview/data/articles.json` | A 324-row demo snapshot from 2026-07-13 with **no forensics**. Not usable for this study. |

Sandboxes cannot reach GCS or the production API; the documented path is
the read-only `workflow_dispatch` diagnostics, which is what this phase used.

### 1.2 Size and date range

| Metric | Count |
|---|---:|
| Rows | 9,512 |
| Filings rows (court documents) | 8,460 |
| — with a full document body (≥1,500 chars) | 3,590 |
| — metadata-only stubs (docket exists, no free body) | 4,870 |
| Non-filings rows (news, tips, social, publications) | 1,052 |
| Rows with an enrichment record (any schema) | 3,176 |
| Rows on the current schema (v3, industry asked) | 1,725 |
| Verdict-true insider cases with ≥1 method | 1,248 |
| — adjudicated or admitted (proven) | 98 |
| — alleged | 1,147 |

Filing years span 2004–2026. The mass sits in 2022–2026 (US, all sectors:
122 / 127 / 138 / 130 / 231 cases by filing year), because the
CourtListener history sweep reaches its 2015 floor and RECAP coverage
thins before 2022. Year counts measure sweep depth as much as incidence;
the trend axis of the rubric has to be read that way.

### 1.3 Source types

| Lane | Source ids | Channel | Country | Notes |
|---|---|---|---|---|
| CourtListener RECAP dockets + opinions | `courtlistener-recap`, `courtlistener-opinions` | filings | US | Query-driven collection (`courtlistener.py::DEFAULT_QUERIES`: "insider trading", "trade secret", "economic espionage", "computer fraud", moonlighting/dual-employment, help-desk social engineering, DPRK IT-worker, spoliation, kickbacks…) plus the `Voya, Voya India` company watchlist. The lexicon shapes every count. |
| PACER purchases | `pacer*` | filings | US | Bodies bought for stubs, budget-capped. |
| CanLII | `canlii-onsc`, `canlii-fc`, `canlii-bcsc`, `canlii-abkb` | filings | CA | Per-court RSS. |
| IndiaCourts | `indiacourts-*` | filings | IN | Lexicon scan over the eCourts open dataset; 25 High Courts; live on sparky since 2026-08-23. |
| News RSS | 63 feeds in `config.py` (Krebs, Dark Reading, HR Dive, Proskauer, SEC press, DOJ, ICO, NCA, OAIC…) | news | — | No court provenance; `country` is null. |
| DataTheftNews | `datatheftnews` | news | — | Trade-secret beat. |
| Tips | 5 Reddit RSS feeds | tips | — | |
| Social | `social-*` | social | — | Scheduled pulls parked; manual only. |
| Publications | `pub-*` | publications | — | Long-form reference docs. |

There is no separate "DOJ release" table: DOJ and SEC press releases arrive
as news rows. Court records are the filings channel. The corpus also
contains no Chinese judicial data; the non-US material is Indian High Court
judgments, Canadian CanLII decisions, and a handful of UK/AU regulator
items in the news channel.

### 1.4 Structured fields already present (per row)

Verified from `shared/schemas/articles.py`, `shared/schemas/forensics.py`,
and the v3 prompt contract in `shared/llm/base.py`.

| Group | Field | Values / notes |
|---|---|---|
| Identity | `link`, `title`, `source_id`, `source_name`, `channel`, `published`, `story_key` | `link` is the stable case id. `story_key` merges duplicate documents of one story. |
| Court metadata | `legal_metadata.{country_code, court_name, court_level, document_kind, case_number, cnr, decision_date, language}` | Present on IndiaCourts rows; US rows resolve country from the source prefix. |
| Lexical tags | `entities.itm_hits[] {id, title, theme, source: lexical/llm}`, `related_detections[]`, `related_preventions[]`, `keywords_hit`, `itm_alignment` | ITM = Insider Threat Matrix (ITM 2.12.0). |
| Facets | `use_cases[]` (overemployment, data-exfiltration, credential-misuse, shadow-it), `insider_type` (malicious / negligent / unintentional), `classification_source` | Heuristic, optionally LLM-refined. |
| Enrichment (projected) | `ai_summary`, `forensics`, `case_record`, `enrichment_history[]` | Append-only history; top-level is the schema-tier-first select-best projection. |
| `forensics` case facts | `is_insider_case`, `source_type`, `legal_posture` (indictment, complaint, plea, conviction, sentencing, civil_suit, settlement, + Indian stages), `actor_profile`, `actor_role`, `access_vector`, `motive_signals[]`, `exfil_channels[]`, `timeframe`, `detection`, `outcome`, `industry` (victim sector enum), `actor_employer_sector` (v3 additive), `actor_citizenship`, `tool_mentions[] {name, role: caught/bypassed/misused/traced}`, `confidence`, `model`, `schema_version` | `detection` is free text, null when the source is silent (Nemotron fill rate is measured, not assumed). |
| `forensics.methods[]` | `action`, `tools[]`, `target_data`, `quantity`, `claim_status` (alleged/admitted/adjudicated/reported/unclear), `evidence_quote`, `evidence_quote_verbatim`, `observables[] {description, artifact, channel, basis}` | Channels: email, chat, network, endpoint, cloud, identity, physical, human. |
| Derived at read time | function × employment state (`normalize_role`), `case_strength` (posture-capped), artifact family, country | Not stored; the ledger recomputes them. |

**What is not in the record and must be read or tagged by hand:** dollar
loss and records affected (only inside free text and `quantity`), time to
detection (only inside `timeframe`/`detection`/`outcome` text), tenure,
regulatory action beyond the docket posture, ATT&CK ids, a
financial-services sub-sector, and the CERT insider-type split. None of
these are enum fields today.

---

## 2. Proposed per-case tagging taxonomy

Principle: reuse the corpus' own normalizers wherever one exists so the
study's numbers reconcile with the EVIDENCE page, and add hand-read tags
only where the record has no field. Each tag says whether it is **derived**
(mechanically from stored fields), **read** (from `ai_summary`, methods and
quotes), or **body** (needs the document body). Values follow the corpus
convention: `unknown` means the record is silent, never a guess.

### 2.1 Actor

| Tag | Values | How |
|---|---|---|
| `function` | executive/officer · manager · technical · front-office/sales · finance/accounting/ops · contractor/vendor · temp/intern · unknown | derived: `normalize_role(actor_role, actor_profile)` — same buckets as EVIDENCE |
| `fs_role` (Voya-specific refinement) | registered-rep/advisor · trader/portfolio-manager · call-center/account-servicing · claims/underwriting · plan-ops/recordkeeping · IT/cloud-admin · TPA/offshore-contractor · executive · other · unknown | read from `actor_role` / `actor_profile` |
| `access_level` | privileged-admin · application-elevated (approver, maker-checker, fund-movement) · standard-user · customer-facing-account-servicing · physical-only · unknown | read from `access_vector`, methods |
| `employment_status` | current · departing · former · contractor · third-party · unknown | derived: `normalize_role` state; **"current" is a default fill** (absence of departure language), flagged as such in every table |
| `tenure` | <1y · 1–5y · >5y · unknown | read from `timeframe`/summary; expect mostly unknown |

### 2.2 Insider type (CERT-style)

| Value | Rule of assignment |
|---|---|
| fraud | funds, accounts or securities: ITM IF016 Misappropriation of Funds, IF016.004 Insider Trading, IF044 Abuse of Decision-Making Authority, PR037.x control circumvention with a money outcome |
| ip-theft | trade secrets, source code, models, strategy: IF022.001, exfil ids with `target_data` = IP |
| data-theft | customer/participant PII, client lists, account data: exfil ids with `target_data` = customer/participant data, ME024.001 Access to Customer Data |
| sabotage | destruction, ransom, lockout: IF014-class, AF ids with destruction, "wiped/deleted" methods |
| espionage | MT017 Espionage, state-directed or foreign-transfer cases |
| collusion-external | ME018 Aiding and Abetting, an outside party named as recipient or co-conspirator (competitor, fraud ring, tippee) |
| unintentional/negligent | MT015 Recklessness, MT022 Boundary Testing, `insider_type` = negligent/unintentional |
| A case can carry two (e.g. data-theft + collusion-external). Primary = the infringement the court document turns on. |

### 2.3 Motive

financial-gain (MT005) · competitive-advantage/new-employer (MT003
Leaver + MT003.002 Resignation with a competitor) · conflict-of-interest/
side-business (MT021, IF038 moonlighting) · revenge/grievance (MT007,
MT023) · coercion (MT012) · recognition/ego (MT024) · ideology/espionage
(MT017) · extortion (MT005.005) · negligence (MT015) · unknown. Derived
from the ITM motive ids on the case, then read-checked against
`motive_signals`.

### 2.4 Target assets

participant/customer PII · account balances or funds · client lists /
book of business · trade secrets, models, source code · MNPI / trading and
portfolio data · credentials and access · plan-sponsor or advisor
relationship data · internal financial records · physical or cash · other ·
unknown. Read from `methods[].target_data` and `exfil_channels`.

### 2.5 Methods / TTPs

Two layers. **Technical (ATT&CK where it applies):** T1078 Valid Accounts ·
T1098 Account Manipulation · T1114 Email Collection · T1213 Data from
Information Repositories · T1039 Data from Network Shared Drive · T1005
Data from Local System · T1074 Data Staged · T1048 Exfiltration over
Alternative Protocol (personal email) · T1567 Exfiltration to Web/Cloud
Service · T1052 Exfiltration over Physical Medium (USB, NAS) · T1530 Data
from Cloud Storage · T1485 Data Destruction · T1070 Indicator Removal ·
T1556 Modify Authentication Process (MFA abuse) · T1621 MFA Request
Generation · T1656 Impersonation. Mapped from `exfil_channels`, ITM means
ids (ME005 Removable Media, IF001.008 File-Sharing Platform, IF003 Media
Capture, IF004.003 Personal NAS, ME021.006 MFA, ME006 Web Access, ME014
Printing) and method actions. **Non-technical:** fund diversion /
fictitious payees · customer account takeover from the servicing seat ·
trading on MNPI · client solicitation on departure · forged or altered
instruments · kickbacks / self-dealing · undisclosed second job ·
approval-workflow abuse (PR037.004) · segregation-of-duties circumvention
(PR037.003) · social engineering of colleagues (PR027 Impersonation). ITM
ids stay on every case as the primary vocabulary; ATT&CK is added where
a technical control maps to it.

### 2.6 Precursors / behavioral indicators noted in the record

resignation or notice given · competitor offer or new employer named ·
termination, discipline or performance dispute · financial stress or debt
· prior policy violations · after-hours / remote access spike · privilege
or access requests out of role · disputes with management · outside
business interest · none stated. Read from `ai_summary`, `motive_signals`,
timeline. Precursors are only counted when the document states them.

### 2.7 Detection and time to detection

`detected_by`: internal-alert (DLP, UAM, SIEM, trade surveillance) ·
internal-audit/reconciliation · coworker or manager report · customer or
plan-sponsor complaint · competitor or new employer (typical in
client-solicitation suits) · external law enforcement or regulator (SEC,
FINRA, FBI) · self-disclosure · litigation discovery · unknown.
`time_to_detection`: <30d · 30–180d · 180d–1y · >1y · unknown. Read from
`forensics.detection`, `timeframe`, `outcome`. Expect a high unknown rate:
complaints rarely say how the plaintiff found out.

### 2.8 Impact

`loss_usd_band`: <$100k · $100k–$1M · $1M–$10M · >$10M · unknown (max
dollar figure stated, alleged vs adjudicated flagged) ·
`records_band`: <1k · 1k–100k · >100k · unknown · `regulatory_action`:
SEC · FINRA · DOL/ERISA · state insurance/AG · OCC/FDIC/Fed · none stated
· `outcome`: the posture-capped `case_strength` plus `forensics.outcome`.

### 2.9 Victim sector and financial-services sub-sector

`industry` is the stored victim-sector enum. `fs_subsector` (new, read):
retirement/recordkeeping · asset-management (adviser, fund, PE/hedge) ·
insurance (life, annuity, P&C, benefits) · banking/lending (bank, credit
union, mortgage) · broker-dealer/wealth · fintech/crypto · other-FS.
`actor_employer_sector` is kept separately so a third-party actor who hit
a financial-services victim is not lost.

### 2.10 Jurisdiction and evidence weight

`country` (US / CA / IN / none) from `resolve_country`; `strength`
(adjudicated-admitted / alleged / reported) from `case_strength`, posture
capped; Indian pre-adjudication stages (bail, quashing, interim
injunction, writ) stay below the adjudicated floor. Non-US rows carry all
tags but enter the annex, not the ranking, unless the pattern is
jurisdiction-independent.

The tagging script that applies the derived layer and emits the read-layer
skeleton is `tools/tag_cases.py` (stdlib only, runs anywhere the corpus
file is readable, including sparky).

---

## 3. Counts by sector (before tagging the full set)

### 3.1 Victim sector, verdict-true v3 cases

| Victim sector | Cases | Share of 1,212 |
|---|---:|---:|
| technology | 235 | 19% |
| manufacturing | 229 | 19% |
| **financial-services** | **191** | **16%** |
| professional-services | 175 | 14% |
| public-sector | 126 | 10% |
| healthcare | 114 | 9% |
| unknown | 43 | 4% |
| retail | 37 | 3% |
| other | 35 | 3% |
| defense | 15 | 1% |
| energy | 12 | 1% |

### 3.2 Financial-services victim cases by jurisdiction

| Jurisdiction | Cases with methods | Proven | Alleged |
|---|---:|---:|---:|
| **US (ranking base)** | **78** | **10** | 68 |
| India (annex) | 109 | 5 | 104 |
| Canada / no provenance | ~3 | — | — |
| Total financial-services (story-merged) | 190 | 13 | 177 |

### 3.3 US financial-services: who and what (78 cases)

Function: executive/officer 28 (3 proven) · front-office/sales 9 (1) ·
manager 8 (2) · technical 7 (2) · contractor/vendor 4 · finance/ops 3 ·
unknown 19 (2). Employment: current (default fill) 40 · former/fired 25 ·
third-party 3 · departing 1 · unknown 9.

Leading ITM techniques: Leaver 40 · Misappropriation of Funds 35 ·
Insider Trading 35 · Resignation 21 · Segregation-of-Duties Circumvention
13 · Aiding and Abetting 12 · Personal Gain 8 · Abuse of Decision-Making
Authority 8 · Recognition 7 · Brand-damaging Public Statements 7 ·
Exfiltration via Media Capture 4 · Review Condition Manipulation 4 ·
File-Sharing Exfiltration 3 · MFA 3 · Undisclosed Concurrent Employment 3
· Removable Media 3 · Extortion 3 · Conflicts of Interest 3. Full table
with exemplar docket titles in `inputs/2026-09-22-corpus-diagnostics.md` §7.

Evidence that carried the 10 proven US financial-services cases: email
logs/content 5, system/file access logs 4, brokerage/trade records 2,
device artifacts 1. Company-held records still carry most proven cases,
but a fifth of the proven set was made on records the employer never
holds (broker records), which is the insider-trading profile's
signature.

### 3.4 Financial-services sub-sector

Not measurable yet: the corpus has no sub-sector field. From the exemplar
titles the 78 US cases visibly include brokerage/wealth (Fidelity
Brokerage v. Meads, J.P. Morgan Securities v. Ali, Focus Financial v.
Mosaic, StoneX v. shipman), mortgage lending (CalCon v. E Mortgage, Guild
v. CrossCountry, Paramount Residential v. Nationwide), insurance (Allstate
v. Fougere, Fortegra v. Pinion, Construction Casualty v. Croteau, CMFG
Life/CUNA), banking (Zions v. Farr), asset management (Canyon Bridge,
Fairstead, NMS Special Opportunity Fund), and crypto (Athena Bitcoin v.
Genesis Coin). No retirement-recordkeeping title is visible in the
exemplar list; that gap is itself a finding for the Voya relevance axis
and will be measured in Phase 2.

---

## 4. What the counts mean for Phases 2–3

1. **Census, not sample.** 78 US cases is small enough to tag every one.
   The India annex (109) is also tagged in full by the script's derived
   layer; hand-read tags for the annex are limited to the 20 highest-
   strength cases.
2. **Expect fewer than ten well-supported profiles.** With 78 cases and a
   5-case floor, the technique table already implies roughly six clusters
   that clear the floor (departing rep/adviser taking the book, insider
   trading on MNPI, executive fund misappropriation, control circumvention
   for fraud, collusion with an outside party, technical exfiltration).
   Candidates such as the privileged IT administrator, the call-center
   account-servicing insider, and the offshore/TPA contractor are exactly
   the exposures the task names for Voya and are thin in this corpus. The
   report will rank what clears the floor and list the rest as weak
   candidates with their case counts, per the task rules.
3. **Collection bias to state up front.** "Insider trading", "former
   employee", "terminate" and "trade secret" are literal collection
   queries, so their volumes are partly manufactured. Within-corpus
   rankings stand; prevalence claims do not.
4. **Employer view adds nothing new.** 166 of the 190 financial-services
   victim cases have an actor employed by the victim; outsiders are 9
   (7 professional-services). Third-party risk will be scored on those
   nine plus the annex, and flagged low-confidence.

---

## 5. Preliminary candidate profiles (unscored, for orientation only)

Drawn from the US financial-services technique clusters and exemplar
titles; every one must be re-derived from the tagged case set before it
is scored. Listed so the reviewer can object to the framing now.

| # | Candidate | Corpus signal (US-FS) |
|---|---|---|
| 1 | Departing adviser / registered rep takes the client book to a competitor | Leaver 40, Resignation 21, Recognition 7, Access to Customer Data; brokerage and RIA titles |
| 2 | Employee trades on MNPI (own book or tipping) | Insider Trading 35, brokerage/trade records 13, SEC Form 4 6 |
| 3 | Executive or officer diverts firm or client funds | Misappropriation of Funds 35, Abuse of Decision-Making Authority 8, Access to Organizational Funds |
| 4 | Operations / approver defeats a control to move money | Segregation-of-Duties Circumvention 13, Approval Workflow Exploitation, Review Condition Manipulation 4 |
| 5 | Insider colludes with an outside party (fraud ring, competitor, tippee) | Aiding and Abetting 12 |
| 6 | Technical staff exfiltrates code, models or data by file-share, USB or NAS | File-Sharing 3, Removable Media 3, Media Capture 4, technical 7 (2 proven) |
| 7 | Moonlighter / undisclosed side business at a competitor or client | Undisclosed Concurrent Employment 3, Conflicts of Interest 3 |
| 8 | Privileged IT / cloud administrator abuse | MFA 3, Privileged Access 1, Account Creation 1 — thin |
| 9 | Call-center / account-servicing insider takes over customer accounts | Access to Customer Data 2, Impersonation 1 — thin |
| 10 | Third-party / TPA / offshore contractor | contractor/vendor 4, third-party 3 — thin |
| 11 | Disgruntled leaver sabotages or extorts | Revenge 2, Extortion 3, timestomping/anti-forensics 1 — thin |
| 12 | Executive makes brand-damaging public statements / misrepresentation | Public Statements 7 (0 proven) — relevance to an ITM program is low |

---

## 6. Blocker and decision needed

The tagging in Phase 2 needs the per-case records (`ai_summary`,
`forensics`, methods, quotes) for the 78 US and 109 Indian
financial-services cases, plus the ability to check each cited case
against its stored record. This session cannot reach the bucket, the
production API, the public site, or CourtListener (all denied at the
network egress). The one path the repo documents — a branch-only Actions
variant that prints public record fields to the job log or uploads a
short-lived artifact — was denied by the session's auto-mode classifier
as data exfiltration, twice, so it was not attempted a third time.

Options, in order of preference:

1. **Approve the branch diagnostic.** A `corpus-sample.yml` variant on
   this branch that prints, for filings rows with `industry=financial-
   services` and `is_insider_case=true` only (~190 rows), the fields the
   public API already serves (title, link, source, published, facets,
   `ai_summary`, projected `forensics`) — no bodies, no history. Roughly
   1 MB in a job log. This session can then finish Phases 2–3 end to end.
2. **Run the export on sparky** and hand the file to the session:
   `python3 analyses/voya-top10-profiles/tools/tag_cases.py data/processed/articles.jsonl --out /tmp/voya` produces `cases-tagged.jsonl`, `evidence-skeleton.csv` and `counts.md`; the JSONL is what the hand-read pass needs.
3. **Re-run this task from an environment with GCS read access.**

Everything in this folder is written so Phase 2 starts from the tagged
JSONL with no rework: `methodology.md` carries the taxonomy above and the
scoring rubric; `tools/tag_cases.py` applies the derived layer and emits
the read-layer skeleton with the profile column empty.
