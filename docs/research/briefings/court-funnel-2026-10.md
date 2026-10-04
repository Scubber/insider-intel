# Most insider cases in the record stop at the complaint

PUBLISHED 2026-10-04 · AS OF 2026-10-04 CORPUS PULL (9,725 documents; 954 verdict-true cases in the slice: 65 adjudicated/admitted, 889 alleged, 0 reported/unclear)

Question: Why so few insider cases reach a verdict: what the posture funnel (reported → alleged → adjudicated) looks like in the record, and what stops a case short.

The slice is every verdict-true insider case whose text names a trade secret, misappropriation, confidential information or proprietary material. That net is wider than the Defend Trade Secrets Act (DTSA, the 2016 federal trade-secret statute): it also catches embezzlement and securities cases that use the word "misappropriation". Read the counts as "insider cases litigated over taken information or money", not as DTSA filings alone.

## Bottom line

Of 954 cases in the slice, 65 (7%) have reached an adjudicated or admitted posture. The other 889 (93%) are alleged, and 613 of those 889 sit at the complaint (407) or interim-injunction (206) stage. The court is where these disputes are filed, not where they are decided. Lex Machina's 2026 report says the same of the federal docket: about 65 percent of trade-secret cases resolve through likely settlement, and the median time to summary judgment is 796 days. For a security, HR or legal reader the practical point is that the record of what insiders do is written almost entirely in complaints and injunction motions. Treat it as a record of allegations, and build the internal evidence that would survive a verdict, because most cases never get one.

## What the record shows

**Convictions, not civil judgments, make up the proven tier.** Among the 65 adjudicated/admitted cases, the posture labels are conviction 17, trial judgment 10, plea 5, with 32 rows whose posture label the enricher could not name and 1 with none. Eleven of the fifteen strongest case cards are criminal prosecutions; three are civil and one is a bar disciplinary appeal. United States v. Xiaorong You (2023, conviction) is typical: a chemical engineer moved BPA-free coating formulas to a personal Google Drive and a USB drive on leaving each employer, and the case closed only after a law-enforcement stop at an airport ([link](https://www.courtlistener.com/opinion/9412735/united-states-v-xiaorong-you/)). The civil exceptions are large or simple: Motorola Solutions v. Hytera (2024, adjudicated) ended in a $764.6 million jury award later reduced to $543.7 million ([link](https://www.courtlistener.com/opinion/9987798/motorola-solutions-inc-v-hytera-communications-corporation-ltd/)); Dravo Bay v. Whalen (2026, trial judgment) was a single adviser who copied client files from Box to a personal Google Drive and downloaded the firm's password file before resigning ([link](https://www.courtlistener.com/opinion/10806797/dravo-bay-dba-blue-rock-financial-group-v-james-whalen/)). What to do: when the loss is large enough to justify a prosecution, preserve evidence to a criminal standard from day one, because that is the path that produces a verdict.

**Executives are sued; engineers are convicted.** Across all 954 cases the largest actor profile is executive/officer, current (225, 24%) and former (124, 13%). Technical roles are 62 cases in the full slice. In the adjudicated tier the picture flips: technical roles are 11 of the 65 (former/fired 7, current 4), executive/officer 11, and the single largest group is "unknown" (23). Read the technical share with the small subgroup in mind, but the direction matches the case cards: engineers who carry source code or formulas to a competitor draw federal prosecution, while officer disputes end in a settled civil docket. What to do: scope offboarding reviews by what the person could carry, not by seniority.

**The company rarely catches it first.** The enricher's detection prose could not be classified for 581 cases (61%). Of the rest, the leading families are law enforcement or a regulator (86), a forensic exam after departure (70), a customer, partner or competitor noticing (67), an audit or log review (42), a monitoring or DLP alert (13), and a colleague or manager report (11). In the adjudicated tier it is a forensic exam after departure (7) and an outside party noticing (7) that lead, with monitoring alerts at 4. Tankmax v. Duran (2022, trial judgment for the defendant) shows the cost of learning late: the firm found out when customers called the ex-employee's new business, had no internal detection to describe, and lost on every claim ([link](https://www.courtlistener.com/docket/63292735/tankmax-inc-v-duran/)). What to do: a departure-triggered forensic review of mail, cloud and removable media is the detection method the record says works; make it routine rather than reactive.

**Named security tools almost never appear as the thing that caught the insider.** In 954 cases, tools named as having caught the actor are three mentions: a Symantec data-loss-prevention system, a WinMagic SecureDoc platform and Cloudflare. Tools named as traced are a Jamf Protect and a Splunk mention. Tools named as misused are plentiful: email 36, WhatsApp 10, Google Drive 8, Zoom 5, Dropbox 3, Telegram 3. United States v. Zheng (2024, conviction posture) is the counter-example that proves the rule: GE's security team found encrypted files on the engineer's machine and then deployed monitoring that fired on AxCrypt use, which produced the evidence of steganography and personal-email exfiltration ([link](https://www.courtlistener.com/opinion/10088833/united-states-v-zheng/)). What to do: if a control fires, keep its output in a form a filing can cite; the record shows that evidence is scarce.

**Email is the channel the filings name.** The exfil-channel field across the slice is led by email and its variants: email 53, personal email 20, personal email account 19, personal Gmail account 10, personal email accounts 7. Google Drive follows at 7, WhatsApp and personal bank accounts at 6, external hard drives at 6. United States v. Haitao Xiang (2023, conviction) is the plain form: confidential research forwarded from work mail to a personal account before a resignation and a flight ([link](https://www.courtlistener.com/opinion/9397097/united-states-v-haitao-xiang/)). What to do: outbound mail to personal webmail in the notice period is the single highest-yield signal in the record.

## Where this meets the outside picture

The post that seeded this briefing gave four reasons there are so few trade-secret suits: owners do not know what they have, plaintiffs cannot afford it, people think taking data is normal, and owners never took reasonable measures. The record speaks to three of them.

On cost, the American Intellectual Property Law Association's 2021 economic survey, as reported by Bloomberg Law, put the median cost of a trade-secret case with $10 million to $25 million at risk at $2.75 million and above $25 million at $4.5 million. That is consistent with a record in which the proven tier is mostly prosecutions: the government, not the victim, funds the cases that reach a verdict.

On "normal to take", a Biscom survey reported by Bitdefender found more than one in four employees took data when leaving a company, and a Ponemon Institute survey reported by Pinsent Masons found half admitted to taking corporate data, with 59 percent saying they believed it was rightfully theirs. The record's channel counts (email first, personal cloud second) and its leading actor profile (current executives and officers) fit a population that does not think of itself as stealing.

On reasonable measures, the appellate direction in 2025 favoured plaintiffs: the Fourth Circuit in Samuel Sherbrooke Corporate v. Mayer reversed a dismissal for inadequate protective measures, as IPWatchdog reported, and the Ninth Circuit in Quintara v. Ruifeng held that a trade-secret claim will rarely be dismissed early for lack of particularity, as Skadden reported. The bar to filing is low. The record shows that the bar to a verdict is what stays high.

On volume, Lex Machina counted 1,552 federal trade-secret filings in 2025, a 20 percent rise and an all-time high, as the ABA Journal reported. The slice's own year counts rise too (141 in 2022, 129, 148, 141, and 230 so far in 2026), but that curve reflects the corpus's own lane coverage at least as much as the courts' caseload.

## Limits

- The slice is a regex over case text. "Misappropriation" pulls in embezzlement, securities fraud and bribery cases alongside trade-secret suits; ITM technique counts in the slice (IF016 at 545, IF016.004 insider trading at 128) show that mix.
- Posture never promotes. A method the enricher stamped "alleged" stays alleged under a conviction posture, so some convicted cases sit in the alleged tier (conviction 10, trial judgment 15 and acquittal 18 appear among the 889). The 65 is a floor on proven cases, not a ceiling.
- Detection families are regex over the enricher's prose, and 61 percent of cases fall outside every family. The detection ranking is a hint about the classified minority, not a finding about all 954.
- Jurisdiction is the source court: US 627, IN 295, CA 1, 31 news rows with none. Indian postures in the slice (quashing 66, bail 37) are pre-adjudication stages and never compare like for like with US verdicts.
- Industry and employer sector are known only on schema-v3 rows; 24 cases were never asked.
- Year counts follow filing or publication dates and the corpus's lane growth. 2026 is a partial year.

## Where to look on the site

- EVIDENCE for the live posture split and technique ledger: https://intel.thederpweb.com/#/evidence
- TOOLING for which control categories cover the techniques in this slice: https://intel.thederpweb.com/#/tooling
- The insider-trading technique dossier, the largest single cluster in the slice: https://intel.thederpweb.com/#/technique/IF016.004

## Sources

1. [Lex Machina 2026 Trade Secret Litigation Report (LexisNexis pressroom)](https://www.lexisnexis.com/community/pressroom/b/news/posts/lex-machina-2026-trade-secret-litigation-report-federal-trade-secret-filings-hit-an-all-time-high-in-2025)
2. [Record number of trade secret cases filed in 2025 (ABA Journal)](https://www.abajournal.com/news/article/record-number-of-trade-secret-case-filed-in-2025-report-finds)
3. [Costs soar for trade secrets, pharma patent suits, AIPLA survey finds (Bloomberg Law)](https://news.bloomberglaw.com/ip-law/costs-soar-for-trade-secrets-pharma-patent-suits-survey-finds)
4. [Fourth Circuit clarifies 'reasonable efforts' standard for DTSA protection (IPWatchdog)](https://ipwatchdog.com/2025/12/02/fourth-circuit-clarifies-reasonable-efforts-standard-dtsa-trade-secret-protection/)
5. [9th Circuit ruling on timing of trade secret disclosures in DTSA cases (Skadden)](https://skadden.com/insights/publications/2025/08/9th-circuit-ruling-offers-guidance)
6. [One in four employees take data when leaving a company, Biscom survey (Bitdefender)](https://www.bitdefender.com/en-us/blog/hotforsecurity/one-in-4-employees-take-data-when-leaving-a-company-survey-shows)
7. [Half of workers admit taking corporate data when leaving, Ponemon survey (Pinsent Masons Out-Law)](https://www.pinsentmasons.com/out-law/news/70-of-workers-would-take-corporate-data-when-leaving-a-company)
8. research-pack run, all postures: https://github.com/Scubber/insider-intel/actions/runs/37219276321
9. research-pack run, adjudicated slice: https://github.com/Scubber/insider-intel/actions/runs/37219277471
10. research-pack run, alleged slice: https://github.com/Scubber/insider-intel/actions/runs/37219278895
