# Research briefing routine — the playbook

This file is the full instruction set for the scheduled research briefing.
A Claude Routine fires it (monthly, fresh session, this repo checked out). The Routine's stored prompt says "follow
docs/research/routine-prompt.md on main", so editing this file changes the
next run with no trigger update. Humans can run the same steps by hand.

The deliverable each run: ONE briefing, archived as a dated Markdown file
in `docs/research/briefings/` on a branch, emailed to the operator by the
`research-mail` workflow, and offered as a pull request.

## Ground rules (these override everything below)

- **Roles, never individuals.** Cite cases by their court title and link.
  Never profile, name or speculate about a person beyond the case title.
  Never print an email local part (the pack already redacts them).
- **Adjudicated, alleged and reported are never mixed.** Every count in the
  briefing names its posture. Never add the three into one number without
  the split beside it.
- **Numbers come from the pack, nothing else.** A number from a web source
  is attributed to that source in the same sentence. Never present an
  outside figure as a corpus figure, or the reverse.
- **Small-n law.** Under 10 cases, quote counts, never percentages.
- **Jurisdiction is the source court, never the actor's nationality.**
- **Frozen numbers live only in the briefing**, under an AS OF dateline.
  Never write a corpus number into any other file (not topics.md, not
  README, not CLAUDE.md).
- **The operator's email address never enters the repository**: not in a
  commit, a file, a PR body or a branch name. Send to the address your
  session context identifies as the user's own; do not write it down.
- **Spend nothing on the production API.** Dispatch `research-pack`; never
  call enrichment, `/reload`, or any write endpoint. Never merge a PR.
- **Voice: executive-plain.** Short sentences. One idea each. Concrete
  subject + verb ("the engineer copied 4,000 files"). Banned: delve,
  leverage, robust, comprehensive, seamless, holistic, landscape, utilize,
  "it's important to note", "in today's world", rhetorical-question
  headers, stacked qualifiers. If a sentence would sound wrong read aloud
  to a general counsel, rewrite it.

## Step 1 — pick the topic

1. Read `docs/research/topics.md` on `main`.
2. List open pull requests (GitHub `list_pull_requests`, state open). Any
   branch named `research/<slug>` means that slug is already taken.
3. Take the first row whose Status is `queued` and whose slug has no open
   `research/<slug>` branch and no file `docs/research/briefings/<slug>*.md`.
4. If every row is taken, write three new topic rows in the same table
   format (a question the corpus can answer, a pack spec), open a PR that
   adds them, and stop. Say so in the session; send no email.

## Step 2 — pull the corpus material

1. Dispatch the `research-pack.yml` workflow on `main` with the topic's
   pack spec (`query`, `techniques`, `country`, `industry`, `posture`,
   `year_from`, `cases=15`, `slug=<slug>`). When the spec lists two slices
   (for example `posture=adjudicated` vs `posture=alleged`, or `country=IN`
   vs `country=US`), dispatch once per slice, one at a time.
2. Wait for each run to complete: list the workflow's runs with
   `actions_list` (`list_workflow_runs`, `research-pack.yml`) every 60–90
   seconds until the newest run's status is `completed`. A run takes two to
   four minutes. Do not dispatch the next slice until the previous run has
   finished — concurrent runs make the "newest run" ambiguous.
3. Read the job log (`actions_list` → `list_workflow_jobs` for the run id,
   then `get_job_logs` with `return_content=true` and `tail_lines=900`) and
   keep only the text between `=== RESEARCH PACK BEGIN ===` and
   `=== RESEARCH PACK END ===`. That text is the pack.
4. If the run failed or the markers are missing, retry the dispatch once.
   If it fails again, stop, send no email, and report the run URL.

## Step 3 — outside context

Run three to six web searches that put the corpus picture in context:
official statistics, recent rulings, surveys, regulator guidance. Keep the
URLs. Prefer primary sources (courts, regulators, bar associations, the
ITM catalog) over vendor marketing. Outside numbers are context, never the
finding.

## Step 4 — write the briefing

Length 800–1,400 words. Markdown. This exact skeleton:

```
# <Title: a plain statement of the finding, not a question>

PUBLISHED <YYYY-MM-DD> · AS OF <pack generated_at date> CORPUS PULL
(<rows> documents; <matched_cases> verdict-true cases in the slice:
<adjudicated> adjudicated/admitted, <alleged> alleged, <reported> reported/unclear)

Question: <the row's question from topics.md>

## Bottom line
<One paragraph. The finding, the number behind it with its posture, and
what a security, HR or legal reader should do differently.>

## What the record shows
<Three to five findings. Each: a bold lead sentence with the count and its
posture; two to four sentences of what it means; one "what to do" sentence.
Cite one or two case cards per finding as "<court title> (<year>, <posture>)"
with the link.>

## Where this meets the outside picture
<Two to four paragraphs weaving in the web sources. Every outside number
names its source inline.>

## Limits
<What the record cannot show: selection bias of the lanes, posture caps,
regex detection families, schema-v3-only fields, small n where it applies,
filing-year lag. Three to six bullets.>

## Where to look on the site
<Two or three links: https://intel.thederpweb.com/#/evidence,
#/tooling, a technique dossier #/technique/<ID> when one is central.>

## Sources
<Numbered list: each web source as a markdown link; the pack as
"research-pack run <url>".>
```

Re-read the draft against the ground rules before sending. Check every
number against the pack. Remove any banned word.

## Step 5 — archive on a branch (so the mail step can read it)

1. Branch `research/<slug>` from `main`.
2. Write the briefing to `docs/research/briefings/<slug>.md`.
3. In `docs/research/topics.md`, change the row's Status from `queued` to
   `drafted <YYYY-MM-DD>`. Change nothing else in that file.
4. Run `pytest tests/test_research_briefings.py` (dateline, Limits,
   Sources, no address, no banned words). Fix and re-run until green.
5. Commit with a message that explains why (what the briefing found, one
   line) and push the branch.

## Step 6 — email it

Dispatch the `research-mail.yml` workflow with `ref` = `research/<slug>`
and input `path` = `docs/research/briefings/<slug>.md`. It renders the
Markdown and sends it over SMTP with the repository's `TRAFFIC_DIGEST_*`
secrets — the recipient is never in your hands. Poll the run as in Step 2
until it completes. If it fails, read its log (the "Email the briefing"
step names any missing secret), retry once, then stop and report.

If a Gmail connector is available in the session, you may ALSO send the
same briefing with it to the user's own address from your session
context; never write that address anywhere.

## Step 7 — open the pull request

Open a PR from `research/<slug>` titled `Research briefing: <title>`. The
body has: the question, the bottom line, the pack run URL, the mail run
URL, the verification line "pytest tests/test_research_briefings.py", and
the standard Claude Code footer. Do not merge. Do not request reviewers.
Finish by stating in the session what was sent and the PR link.

## Cadence and edits

Monthly on the 3rd (after the 1st's traffic digest and the nightly corpus
refresh). To change cadence, edit the Routine's schedule; to change what a
run does, edit this file. To add topics, add rows to `topics.md`.
