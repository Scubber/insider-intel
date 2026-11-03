# The files leave before the notice does

PUBLISHED 2026-11-03 · AS OF 2026-11-03 CORPUS PULL (10,185 documents; 975 verdict-true cases in the departure slice: 80 adjudicated/admitted, 893 alleged, 2 reported/unclear; the all-cases comparison slice holds 1,393: 113 adjudicated/admitted, 1,276 alleged, 4 reported/unclear)

Question: The last thirty days: what departing employees take, through which channel, and how it was found.

The departure slice is every verdict-true insider case whose text mentions a resignation, a notice period, a last day, two weeks, a termination or a departure. That net is coarse: "termination" also catches contracts and leases, so the slice is "cases where leaving is part of the story", not "cases caused by leaving". The all-cases slice is the comparison.

## Bottom line

Seventy percent of the record's insider cases (975 of 1,393) mention a departure. In the adjudicated tier, the copying happened while the person was still employed and the employer found out afterwards, usually from someone outside the company. The channel is the same one the filings name everywhere else: email to a personal account first, then personal cloud drives and USB media. Monitoring alerts surface in 10 of the 975 departure cases. The practical point for a security, HR or legal reader is that a notice-period review starts too late. The window that matters opens before the notice, and the record says the evidence is already on a personal drive by the time HR hears the word "resign".

## What the record shows

**Departure is in most cases, and the departed are a larger share of the actors.** In the departure slice, actors tagged former or fired are 415 of 975 (43%), against 532 of 1,393 (38%) in the all-cases slice. The leading departed profile is executive or officer, former or fired (150, 15% of the slice), then unknown function, former or fired (122, 13%), manager (51), contractor or vendor (30), technical (28) and front-office or sales (28). Current executives and officers still top the whole table at 224 (23%). What to do: an offboarding review that only covers engineers misses the profile the record names most, which is the officer who leaves.

**The proven cases copied first and resigned second.** United States v. Haitao Xiang (2023, conviction) sent confidential research from work email to a personal account before resigning in June 2017 and was stopped at an airport afterwards ([link](https://www.courtlistener.com/opinion/9397097/united-states-v-haitao-xiang/)). United States v. Yu (2025, conviction) saved chip-design files to a personal computer, renamed them after cartoon characters, resigned in July 2017 and kept them to start a competitor ([link](https://www.courtlistener.com/opinion/10754171/united-states-v-yu/)). United States v. Yu Xue (2022, conviction) took about 200 documents while still employed ([link](https://www.courtlistener.com/opinion/7853265/united-states-v-yu-xue/)). Dravo Bay v. Whalen (2026, trial judgment) copied client files from Box to a personal Google Drive and downloaded the firm's password file before a resignation effective 14 March 2025 ([link](https://www.courtlistener.com/opinion/10806797/dravo-bay-dba-blue-rock-financial-group-v-james-whalen/)). Motorola Solutions v. Hytera (2024, adjudicated) turned on engineers who downloaded thousands of documents before leaving ([link](https://www.courtlistener.com/opinion/9987798/motorola-solutions-inc-v-hytera-communications-corporation-ltd/)). What to do: treat the notice as confirmation that a review is due for the weeks already past, not as the start of a watch.

**The employer learned late, and from outside.** The enricher's detection prose cannot be classified for 594 departure cases (61%). Among the rest, the leading families are law enforcement or a regulator (98), a customer, partner or competitor noticing (73), a forensic exam after departure (73), an audit or log review (39), a colleague or manager report (13), and a monitoring or DLP alert (10). The proven cards match the pattern: Whalen's firm opened its Box logs after clients received his announcement cards; Xiang and Xiaorong You were stopped at airports after leaving ([link](https://www.courtlistener.com/opinion/9412735/united-states-v-xiaorong-you/)); Yu's files were found under a search warrant. United States v. WILLIAMS (2025, plea) is the long-tenure counter-case: a current general manager moved trade secrets from a secure network to a personal device from April 2022, and the company learned in October 2024 when a software broker turned up holding its products ([link](https://www.courtlistener.com/docket/71644575/united-states-v-williams/)). What to do: a departure-triggered forensic review of mail, cloud and removable media is the one internal method the record credits with finding the theft; make it routine for every exit above a risk threshold.

**Email is the channel, personal cloud is second, USB is third.** The exfil-channel field in the departure slice reads email 50, personal email account 19, personal email 19, personal Gmail account 9, personal email accounts 6, then Google Drive 7, pen drive 7, external hard drive 6, personal devices 5. Tools named as misused are email 36, Google Drive 8, WhatsApp 7, Gmail 6, OneDrive 6, pen drive 6, Zoom 5. Tools named as having caught the insider are four mentions across the 975: a Symantec data-loss-prevention system, a WinMagic SecureDoc platform, Cloudflare, and an HPE ethics hotline. What to do: outbound mail to personal webmail and sync-client uploads to personal Google Drive or OneDrive are the two signals worth a standing rule in the weeks around any departure.

**A quarter of departure cases sit at the injunction stage.** Legal posture labels in the slice are complaint 434, interim injunction 217, unknown 116, quashing 53 (an Indian pre-trial stage), trial judgment 24, conviction 21, indictment 18, bail 18 and acquittal 15. The 217 injunction-stage cases are employers going to court fast to stop a departed employee using what they took, which is the civil remedy the record shows working most often short of a verdict. What to do: the evidence that wins a temporary restraining order is the same evidence a departure review produces, so the review is also the litigation file.

## Where this meets the outside picture

The timing finding has a long pedigree. The Software Engineering Institute's 2011 preliminary model of insider theft of intellectual property, by Moore, Cappelli and Caron, found that most insiders who stole intellectual property did so within thirty days of resignation, as Dawn Cappelli said in BankInfoSecurity's "30-day warning" interview. The SEI's 2012 follow-on pattern for increased monitoring of departing insiders recommends a review window of at least thirty days before and after the departure. The record here agrees with the direction: the copying precedes the notice in every proven card above.

Vendor telemetry puts numbers on the window the court record cannot. Cyberhaven's research, as reported by CSO Online, measured an 83.1 percent rise in data-exfiltration incidents in the two weeks before an employee gave notice and a 37.7 percent rise between notice and last day. Netskope's 2021 study of 58,314 leavers found 15 percent moved data to personal cloud applications, some uploading three times their usual volume in the last thirty days, with personal Google Drive and OneDrive the top destinations, and its analysis concluded that a fourteen-day lookback catches only about half the files moved. Those are vendor figures from their own customers' traffic, and the record's channel counts (personal email first, Google Drive and OneDrive next) are consistent with them.

On process, NIST SP 800-53's PS-4 control, Personnel Termination, as summarised by UpGuard, requires disabling access and retrieving property on separation and allows an exit interview. Nothing in it requires a look back at what left before the separation. The record suggests that gap is where the cases live.

On the civil remedy, Lex Machina's trade-secret data as reported by IP CloseUp found plaintiffs obtained a temporary restraining order about 62 percent of the time they asked and a preliminary injunction about 55 percent of the time. The 217 injunction-stage cases in this slice are the same motion from the employer's side.

## Limits

- The departure regex is coarse. "Termination" matches contract and lease language, and "last day" and "two weeks" are generic, so the 975 includes cases where leaving is incidental. The comparison slice shows how far the departure slice's shape differs from the whole: not far.
- The record cannot time-stamp the copying against the notice. "Before the notice" rests on the proven case cards, not on a field the enricher fills.
- Posture never promotes. A method stamped "alleged" under a conviction posture stays alleged, so conviction 21 and trial judgment 24 appear among the alleged labels. The 80 is a floor on proven departure cases.
- Detection families are regex over the enricher's prose; 61 percent of cases fall outside every family. The detection ranking describes the classified minority.
- Jurisdiction is the source court: US 691, IN 268, CA 1, 15 news rows with none. Indian postures (quashing 53, bail 18) are pre-adjudication stages and never compare like for like with US verdicts.
- Industry is known only on schema-v3 rows; 34 cases were never asked. Year counts follow filing dates and the corpus's lane growth; 2026 is a partial year.

## Where to look on the site

- EVIDENCE for the live posture split and technique ledger: https://intel.thederpweb.com/#/evidence
- TOOLING for the control categories that cover the departure techniques: https://intel.thederpweb.com/#/tooling
- The moonlighting technique dossier, which the departure slice contains almost whole (65 of the corpus's 69 IF038 cases): https://intel.thederpweb.com/#/technique/IF038

## Sources

1. [A Preliminary Model of Insider Theft of Intellectual Property, SEI 2011 (archived PDF)](https://webarchive.library.unt.edu/web/20120112123946/http://www.cert.org/archive/pdf/11tn013.pdf)
2. [A Pattern for Increased Monitoring for Intellectual Property Theft by Departing Insiders, SEI 2012 (DTIC)](https://apps.dtic.mil/sti/html/tr/ADA585504/index.html)
3. [Insider Threat: 30-Day Warning (BankInfoSecurity)](https://www.bankinfosecurity.com/blogs/insider-threat-30-day-warning-p-1106)
4. [Netskope research shows growing risk of company data exposure as employees depart (press release, 2021)](https://www.netskope.com/press-releases/netskope-research-shows-growing-risk-of-company-data-exposure-as-employees-depart-their-jobs)
5. [Insider threats packing their bags with corporate data (Netskope blog)](https://www.netskope.com/blog/insider-threats-packing-their-bags-with-corporate-data)
6. [One in 10 employees leaks sensitive company data every 6 months, Cyberhaven report (CSO Online)](https://www.csoonline.com/article/573621/one-in-10-employees-leaks-sensitive-company-data-every-6-months-report.html)
7. [NIST SP 800-53 PS-4: Personnel Termination (UpGuard summary)](https://www.upguard.com/compliance/nist-sp-800-53/ps/ps-4)
8. [73% of trade secret suits now contain federal claims; TRO and PI rates (IP CloseUp, Lex Machina data)](https://ipcloseup.com/2021/06/29/73-of-trade-secret-suits-now-contain-federal-claims-74-of-merit-judgements-result-in-injunctions/)
9. research-pack run, departure slice: https://github.com/Scubber/insider-intel/actions/runs/41657371221
10. research-pack run, all cases: https://github.com/Scubber/insider-intel/actions/runs/41657376831
