# Corpus diagnostics read on 2026-09-22 (AS OF the 2026-09-22 08:00Z refresh)

Every number here was printed by one of the repo's read-only Actions
diagnostics run on 2026-09-22 between 14:30Z and 14:38Z and copied from the
job log. Nothing here is hand-computed. Run ids are given so each table can
be re-read; the same dispatch re-computes them on the live corpus.

| Diagnostic | Run | Inputs |
|---|---|---|
| corpus-count | https://github.com/Scubber/insider-intel/actions/runs/35740770681 | (none) |
| corpus-industry (victim) | https://github.com/Scubber/insider-intel/actions/runs/35740751247 | financial-services, by=victim, top=25 |
| corpus-industry (employer) | https://github.com/Scubber/insider-intel/actions/runs/35740759922 | financial-services, by=employer, top=25 |
| corpus-find | https://github.com/Scubber/insider-intel/actions/runs/35740764401 | term=Voya, max_hits=60 |
| evidence-ledger (global) | https://github.com/Scubber/insider-intel/actions/runs/35741337296 | top=40 |
| evidence-ledger (US, financial-services) | https://github.com/Scubber/insider-intel/actions/runs/35741524790 | branch `claude/nice-lovelace-pz1wjc`, country=US, industry=financial-services |
| evidence-ledger (US, all sectors) | https://github.com/Scubber/insider-intel/actions/runs/35741528273 | branch, country=US, industry=all |
| evidence-ledger (IN, financial-services) | https://github.com/Scubber/insider-intel/actions/runs/35741531642 | branch, country=IN, industry=financial-services |

## 1. Corpus size (corpus-count)

| Metric | Count |
|---|---:|
| Corpus rows (`processed/articles.jsonl`, last-line-wins dedupe) | 9,512 |
| Filings-channel rows | 8,460 |
| Filings with a full body (clean_text >= 1,500 chars) | 3,590 |
| Filings that are metadata-only stubs (no free RECAP body) | 4,870 |
| Filings enriched and current (Nemotron, schema v3) | 1,572 |
| Filings enriched on an older model (stale) | 1,067 |
| Filings never enriched | 5,821 |
| Non-filings rows (news / tips / social / publications) | 1,052 |

## 2. Case funnel (corpus-industry, both modes agree)

| Stage | Rows |
|---|---:|
| JSONL lines | 9,512 |
| Deduped links | 9,512 |
| With forensics (any schema) | 3,176 |
| Schema v3+ tier (industry was asked) | 1,725 |
| Verdict-true insider cases (v3) | 1,212 |
| With >= 1 extracted method | 1,198 |
| Cases after story merge | 1,194 |
| Verdict-true rows below v3 (industry NOT ASKED) | 51 |

Global ledger basis (evidence-ledger, all schemas): 9,512 rows -> 3,176
enriched -> 1,263 verdict-true -> **1,248 cases with methods**
(adjudicated/admitted 98, alleged 1,147, reported/unclear 3). Enrichment
models on the case set: Nemotron 3 Super x1,200, Haiku 4.5 x32, Qwen3.6 x10,
Qwen3.8 x3, Sonnet 5 x3. Verbatim-verified evidence quotes: 64% of 3,703.

## 3. Victim sector of verdict-true v3 rows

| Industry (victim) | Rows |
|---|---:|
| technology | 235 |
| manufacturing | 229 |
| financial-services | 191 |
| professional-services | 175 |
| public-sector | 126 |
| healthcare | 114 |
| unknown | 43 |
| retail | 37 |
| other | 35 |
| defense | 15 |
| energy | 12 |

Insider's own employer sector (v3 additive field; 927 rows unknown/not asked):
financial-services 170, professional-services 40, technology 24,
manufacturing 16, healthcare 13, public-sector 13, defense 3, other 3,
retail 2, energy 1.

## 4. Financial-services victim slice by jurisdiction (evidence-ledger)

| Slice | Rows | Cases with methods | Adjudicated/admitted | Alleged | Role signal present |
|---|---:|---:|---:|---:|---:|
| Global, all sectors | 9,512 | 1,248 | 98 | 1,147 | 1,051 |
| US, all sectors | 6,860 | 748 | 60 | 688 | 657 |
| **US, financial-services** | 82 | **78** | **10** | 68 | 69 |
| IN, financial-services | 111 | 109 | 5 | 104 | 90 |
| Global, financial-services (corpus-industry, story-merged) | 191 | 190 | 13 | 177 | — |

The financial-services victim slice is 190 cases, of which 78 are US court
records and 109 are Indian High Court records. The remaining ~3 are Canadian
(CanLII) or carry no court provenance.

## 5. Actor profile tables — financial-services victims (corpus-industry, by=victim; 190 cases)

| Function | Employment state | Cases | Adjud./adm. | Alleged | Share |
|---|---|---:|---:|---:|---:|
| executive/officer | current (default fill) | 41 | 3 | 38 | 22% |
| manager | current (default fill) | 36 | 4 | 32 | 19% |
| unknown | former/fired | 18 | 2 | 16 | 9% |
| manager | former/fired | 15 | 0 | 15 | 8% |
| executive/officer | former/fired | 14 | 0 | 14 | 7% |
| finance/accounting/ops | current (default fill) | 10 | 2 | 8 | 5% |
| front-office/sales | former/fired | 6 | 1 | 5 | 3% |
| technical | current (default fill) | 6 | 1 | 5 | 3% |
| front-office/sales | current (default fill) | 4 | 0 | 4 | 2% |
| contractor/vendor | former/fired | 2 | 0 | 2 | 1% |
| contractor/vendor | third-party | 2 | 0 | 2 | 1% |
| executive/officer | departing | 2 | 0 | 2 | 1% |
| finance/accounting/ops | former/fired | 2 | 0 | 2 | 1% |
| executive/officer | third-party | 1 | 0 | 1 | 1% |
| technical | former/fired | 1 | 1 | 0 | 1% |
| temp/intern | current (default fill) | 1 | 0 | 1 | 1% |
| unknown | third-party | 1 | 0 | 1 | 1% |
| unknown | unknown | 28 | 2 | 26 | 15% |

Victim x employer for these 190: same firm 166 (87%), other sector 9
(professional-services 7, public-sector 1, technology 1), employer
unknown/not asked 15.

Motive stamps by profile (ITM motive ids, cases): executive/current MT003
x19, MT003.002 x13, MT005 x7, MT021 x4, MT012 x4, MT007 x4; manager/current
MT003 x13, MT012 x10, MT003.002 x9, MT005 x6, MT021 x3; executive/former
MT003 x12, MT003.002 x9; unknown/former MT003 x12, MT005 x4; manager/former
MT005 x6, MT003 x4; finance-ops/current MT003 x6, MT012 x2.

Legal posture by profile (cases): executive/current complaint x15,
interim_injunction x15, quashing x4, trial_judgment x2, plea x1,
indictment x1, acquittal x1; manager/current unknown x9, quashing x9,
interim_injunction x6, complaint x4, bail x4, acquittal x2, writ_review x1,
charge_sheet x1; manager/former quashing x6, interim_injunction x5, unknown
x4; technical/current complaint x2, conviction x2, indictment x1, bail x1;
finance-ops/current interim_injunction x3, bail x2, indictment x1, quashing
x1, complaint x1.

## 6. Actor profile tables — financial-services EMPLOYERS (corpus-industry, by=employer; 169 cases)

| Function | Employment state | Cases | Adjud./adm. | Alleged | Share |
|---|---|---:|---:|---:|---:|
| executive/officer | current (default fill) | 36 | 3 | 33 | 21% |
| manager | current (default fill) | 34 | 2 | 32 | 20% |
| unknown | former/fired | 17 | 2 | 15 | 10% |
| manager | former/fired | 15 | 0 | 15 | 9% |
| executive/officer | former/fired | 12 | 0 | 12 | 7% |
| finance/accounting/ops | current (default fill) | 9 | 2 | 7 | 5% |
| front-office/sales | former/fired | 5 | 1 | 4 | 3% |
| technical | current (default fill) | 5 | 1 | 4 | 3% |
| front-office/sales | current (default fill) | 4 | 0 | 4 | 2% |
| executive/officer | departing | 2 | 0 | 2 | 1% |
| finance/accounting/ops | former/fired | 2 | 0 | 2 | 1% |
| (six one-case rows) | | 6 | 1 | 5 | |
| unknown | unknown | 23 | 2 | 21 | 14% |

Victim sector for these 169: financial-services 166, professional-services
1, technology 1, unknown 1.

## 7. US financial-services ledger (78 cases; the ranking base)

Function: executive/officer 28 (3 proven), front-office/sales 9 (1),
manager 8 (2), technical 7 (2), contractor/vendor 4 (0),
finance/accounting/ops 3 (0), unknown 19 (2).
Employment state: current 40 (6 proven), former/fired 25 (3), third-party 3,
departing 1, unknown 9.

Technique frequency (ITM id, cases, adjudicated/admitted, exemplar titles as
the ledger prints them):

| ITM | Cases | Adj. | Exemplars (docket titles) |
|---|---:|---:|---|
| MT003 Leaver | 40 | 5 | Fidelity Brokerage Services LLC v. Meads; Greystone Servicing Company LLC v. CWCapital Asset Management LLC; CalCon Mutual Mortgage LLC v. E Mortgage Capital Incorporated |
| IF016 Misappropriation of Funds | 35 | 2 | Fidelity Brokerage Services LLC v. Meads; CalCon Mutual Mortgage LLC v. E Mortgage Capital Incorporated; L5E, LLC v. Shaw |
| IF016.004 Insider Trading | 35 | 5 | Fidelity Brokerage Services LLC v. Meads; Susquehanna Securities, LLC v. John Does 1 Through 100; Zou v. Girouard |
| MT003.002 Resignation | 21 | 3 | Fidelity Brokerage Services LLC v. Meads; Greystone Servicing Company LLC v. CWCapital Asset Management LLC; L5E, LLC v. Shaw |
| PR037.003 Segregation of Duties Circumvention | 13 | 1 | Zou v. Girouard; L5E, LLC v. Shaw; Focus Financial Partners LLC v. Mosaic Value Partners LLC |
| ME018 Aiding and Abetting | 12 | 2 | Zou v. Girouard; Benjamin Chow v. Canyon Bridge Capital Management LLC; Guild Mortgage Company v. CrossCounty Mortgage |
| MT005 Personal Gain | 8 | 0 | The Fortegra Group, Inc. v. Pinion Risk Consulting Limited; Zions Bancorporation National Association v. Farr; PARAMOUNT RESIDENTIAL MORTGAGE GROUP, INC. v. NATIONWIDE MORTGAGE BANKERS, INC. |
| IF044 Abuse of Decision-Making Authority | 8 | 2 | GOODMAN v. FORMAN; United States v. D'Ambrosio; Allstate Insurance Company v. Fougere |
| MT024 Recognition | 7 | 1 | Fidelity Brokerage Services LLC v. Meads; Focus Financial Partners LLC v. Mosaic Value Partners LLC; J.P. MORGAN SECURITIES LLC v. ALI |
| IF012 Public Statements Resulting in Brand Damage | 7 | 0 | Zou v. Girouard; GOODMAN v. FORMAN; LINHARES v. CREDIT SUISSE GROUP AG |
| IF003 Exfiltration via Media Capture | 4 | 0 | THE ONLI CORPORATION v. GRIFFIN; United States v. GALLO; Rel. Ins., Inc. v. Pilot Risk Mgmt. Consulting, LLC |
| PR037.002 Review Condition Manipulation | 4 | 0 | Cmfg Life Insurance Company / Cuna Brokerage Services; United States v. GALLO; Construction Casualty Insurance, LLC v. Croteau |
| IF001.008 Exfiltration via File-Sharing Platform | 3 | 0 | L5E, LLC v. Shaw; Athena Bitcoin, Inc v. Genesis Coin, Inc.; United States v. Chai |
| ME021.006 Multi-Factor Authentication | 3 | 0 | L5E, LLC v. Shaw; The Fortegra Group, Inc. v. Pinion Risk Consulting Limited; United States v. HO |
| ME024 Access | 3 | 0 | L5E, LLC v. Shaw; PARAMOUNT RESIDENTIAL MORTGAGE GROUP v. NATIONWIDE MORTGAGE BANKERS; NMS Special Opportunity Fund, LP v. Global Structured Products (Jersey) Limited |
| IF038 Undisclosed Concurrent Employment | 3 | 0 | The Fortegra Group, Inc. v. Pinion Risk Consulting Limited; Zions Bancorporation National Association v. Farr; Bamford v. Penfold, L.P. |
| ME005 Removable Media | 3 | 0 | Nasdaq Private Market, LLC v. The Hiive Company Limited; StoneX Group Inc. v. shipman; AssuredPartners of Oregon, LLC v. Reese |
| MT005.005 Extortion | 3 | 1 | THE ONLI CORPORATION v. GRIFFIN; United States v. John Afriyie; Athena Bitcoin, Inc v. Genesis Coin, Inc. |
| MT021 Conflicts of Interest | 3 | 0 | James B. Oswald Co. v. Dennis Neate; Bayport Financial Service (USA) Inc. v. BayBoston Managers, LLC; Moran v. Kalshi Inc. |
| ME024.001 Access to Customer Data | 2 | 0 | Fidelity Brokerage Services LLC v. Meads; Construction Casualty Insurance, LLC v. Croteau |
| MT023 Revenge | 2 | 0 | THE ONLI CORPORATION v. GRIFFIN; KUEH v. SHAO |
| PR025 File Download | 2 | 2 | Dravo Bay d/b/a Blue Rock Financial Group v. James Whalen; Fairstead Capital Management LLC v. Blodgett |
| MT012 Coercion | 2 | 0 | Batanjany v. Clear Street Management LLC; KUEH v. SHAO |
| IF015 Theft | 2 | 0 | Zions Bancorporation National Association v. Farr; The Moore Charitable Foundation v. PJT Partners |
| ME024.008 Access to Organizational Funds | 2 | 0 | Construction Casualty Insurance, LLC v. Croteau; Athena Bitcoin, Inc v. Genesis Coin, Inc. |
| one-case ids | 1 each | | IF009 (CalCon Mutual Mortgage); MT003.005 (Fortegra); ME001.001 (Nasdaq Private Market v. Hiive); PR027, PR041 (ONLI v. Griffin); IF023 (Batanjany v. Clear Street); IF025 (NRA Group LLC v. Nicole Durenleau, adjudicated); PR042 (Kelly v. Dorsey); AF001.004 (Rel. Ins. v. Pilot Risk); AF032, ME003.001, ME006 (StoneX v. shipman); ME025.002 (Bayport v. BayBoston); PR020.002 (Xiong v. Vivos xPoint); ME007 (NMS Special Opportunity Fund) |

Evidence record classes touched (cases / adjudicated): email logs or
content 38/5; system or file access logs 24/4; brokerage or trade records
13/2; public statements vs internal records 9/0; workstation or device
artifacts 8/1; SEC Form 4 or insider-transaction filings 6/0; authentication
logs 3/0; central audit trails 3/0; bank transaction records 3/0; removable
media 2/0; CRM access logs 2/0; personal messaging 2/0. Channel coverage:
network 48, identity 29, human 29, email 23, endpoint 23, cloud 7, physical
4, chat 2.

Filing-year mix (cases): 2022 10, 2023 13, 2024 7, 2025 13, 2026 35.

## 8. US all-sector comparator (748 cases, 60 proven)

Function: executive/officer 346 (46%), manager 75, contractor/vendor 59,
technical 45, front-office/sales 42, finance/accounting/ops 10, temp/intern
3, unknown 168. Employment: current 314, former/fired 306, third-party 36,
departing 1, unknown 91. Top techniques: MT003 439, IF016 367, IF016.004
223, MT003.002 166, PR037.003 146, IF001.008 100, IF012 96, MT021 94, IF044
79, ME018 77, MT024 74, ME005 57, IF003 49, MT005 43, IF038 41. Proven cases
by evidence class: email 28 of 60, system/file access logs 19, device
artifacts 13, brokerage 5, USB 5. Filing-year cases: 2022 122, 2023 127,
2024 138, 2025 130, 2026 231.

## 9. India financial-services annex base (109 cases, 5 proven)

Function: manager 42 (39%), executive/officer 29, finance/accounting/ops 9,
technical 1, front-office 1, unknown 27. Employment: current 57, former 31,
unknown 19. Techniques: MT003 51, IF016 48, IF044 28, MT005 27, MT003.002
26, MT012 21, PR037.004 11, IF003 8, MT021 7, MT007 6, PR037.003 5, IF038 2.
Evidence classes: bank transaction records 15, system/file access 12, email
8, audit trails 6, account-opening/KYC 5, loan sanction/approval/
disbursement records (several 2-4 case rows), modified cheque records 4.
Postures are dominated by bail, quashing, interim injunction and writ review
(pre-adjudication stages weighted below the adjudicated floor).

## 10. Voya-named rows (corpus-find, whole-word "Voya")

5 filings name Voya in the stored text; 1 is verdict-true insider
(Jadlow v. Danker, https://www.courtlistener.com/docket/64346418/jadlow-v-danker/,
body-only mention), 4 are un-enriched dockets where Voya appears only in the
body (JOANN Inc.; Argos Holdings Inc. v. Wilmington Trust; Commercial Real
Estate Exchange v. USDC C.D. Cal.; Linquanti-Guisto v. All Children's Health
System). `COURTLISTENER_COMPANY_WATCHLIST` collects Voya-named US filings by
construction, so this count is a collection artifact, not a base rate.
