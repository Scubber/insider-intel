# insider-intel

**Insider-threat guidance built from real court cases.**

Most insider-threat advice is vendor opinion. insider-intel is built from
what actually reached a courtroom: employees and contractors who stole data,
took secrets to a competitor, ran a second job on company time, or sabotaged
systems on the way out. For each case we break down what the insider did,
the trail they left, and what finally caught them. Every claim links back to
the filing it came from.

**Live site:** [insider-intel.net](https://insider-intel.net) · the ABOUT
page carries the feed link for following new cases.

Built for security, HR, legal, and investigations teams. Readable by anyone.

---

## Four jobs

The site is organised around the four things an insider-threat program has
to do.

| Job | Question it answers | Where to look |
|---|---|---|
| **Build a program** | Which controls actually caught insiders in real cases? | EVIDENCE |
| **Detect** | What warning signs revealed each tactic, and in which cases? | MATRIX › Detections |
| **Prevent** | Which safeguards would have stopped the tactics insiders really used? | MATRIX › Preventions |
| **Hunt** | It may already have happened. What should I look for in my own logs? | STREAM → WORKBENCH |

## What's on the site

- **STREAM** — the live feed of insider court cases and news, newest first.
  Each case carries a plain-language analyst note, a signal score, and a
  provenance stamp saying where it came from.
- **MATRIX** — the [Insider Threat Matrix™](https://insiderthreatmatrix.org/)
  browsed stage by stage (Motive · Means · Preparation · Infringement ·
  Anti-Forensics). Every tactic opens a dossier with the real cases where it
  was used, what caught it, and plain-language "how to spot it / how to
  counter it" guidance distilled from those cases.
- **EVIDENCE** — the corpus-wide research view. Who commits insider cases
  (by role and employment stage, never by name), which tactics show up most,
  what records and witnesses proved them, and which detections the case
  record actually corroborates. A FINDINGS report reads like a short brief:
  bottom line first, then numbered findings. Jurisdiction tabs give each
  court system its own report.
- **TOOLING** — security-product categories ranked by how much of the
  observed case record their controls cover, plus each named product's
  court-filing record: which caught insiders, which got bypassed.
- **WORKBENCH** — save cases from STREAM and compare them side by side as a
  MODUS OPERANDI case study: per-case methods, observables, and legal posture.

## What's in the corpus

- **Court records** from the United States (federal dockets and opinions),
  Canada, and India, scanned daily for insider cases.
- **Prosecutor and regulator feeds** from the US, UK, Canada, and Australia.
- **Insider-relevant news** from curated security, legal, and HR sources.
- **Reference publications** — the standard program-building guides.
- **First-person accounts** flagged one at a time from social platforms
  (overemployment and data-theft confessions).

Each case is analysed once, at ingest, into a structured forensic record:
actions taken, tools involved, quantities, observables, legal outcome, and
its mapping to the Matrix. The site reads those stored records. No analysis
runs while you browse, so the numbers are reproducible and never stale.

## Trust rules

These hold on every page.

- **Adjudicated vs alleged are never conflated.** Every count is split by
  case strength. What a court confirmed is marked differently from what a
  filing merely alleged, with an explicit legend.
- **Roles, never individuals.** The research surfaces describe actors by
  function and employment stage. There are no persona graphs and no entity
  resolution across cases.
- **Small numbers stay honest.** Percentages are suppressed below a minimum
  sample size, and a finding that cannot clear the floor says nothing.
- **Selection bias is stated first.** Court data over-represents what gets
  litigated. The methodology notes say so before any number does.
- **Receipts, always.** Every claim links to the filing or article behind it.

## Attribution and licensing

Insider Threat Matrix™ is owned by Forscie Limited. This project maps cases
to the Matrix and is not affiliated with or endorsed by Forscie. See
[`NOTICE`](NOTICE).

US court records come from [CourtListener](https://www.courtlistener.com/)
(Free Law Project). Indian judgments come from the
[Indian High Court Judgments](https://registry.opendata.aws/indian-high-court-judgments/)
open dataset (CC BY). Canadian decisions come from CanLII court feeds.

## For developers

```bash
make up      # local stack
make test    # same checks CI runs
```

Setup, conventions, and contributor notes live in [`docs/`](docs/).

Security review: the repo ships an audit image and threat model for
Anthropic's [OSS Scanner](https://github.com/anthropics/oss-scanner) in
[`.oss-scanner/`](.oss-scanner/README.md). To report a vulnerability
yourself, contact the maintainer privately rather than opening a public
issue.

Built and run by [Tim Carreira](https://github.com/Scubber).
