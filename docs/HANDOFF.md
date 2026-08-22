# HANDOFF — current state for the next session / IDE / LLM

**Read this to pick up where the last session left off.** It is the live
operational state; [`../CLAUDE.md`](../CLAUDE.md) is the architecture/operating
manual, [`hosting.md`](hosting.md) the production detail, and the merged PRs
(linked below) are the diff-level changelog.

**Last updated:** 2026-08-22 · **Repo:** `Scubber/insider-intel` · **Prod:**
API on Cloud Run (`insider-intel-api`, 2Gi), UI on GitHub Pages
(`intel.thederpweb.com`), corpus in GCS, corpus refresh on the **DGX Spark**
(cron **daily** — reduced from the cutover's 4×/day, operator-confirmed
2026-08-22; Cloud Scheduler paused as rollback).
**Rollback checkpoints:** `checkpoint/v1.1-design-2026-08-10` (the current
working design, blessed before the next UI redesign — restore `web/**` from
here if the redesign goes sideways) · `checkpoint/v1.0-parked` (pre-August
prod).

---

## Live production state

| Area | State |
|---|---|
| **Refresh tenant** | **DGX Spark ("sparky") since 2026-08-16** — cron **daily** at 08:00 UTC (operator-confirmed 2026-08-22; the Pages snapshot fires 08:40 UTC ≈ 40min after) runs `scripts/spark_refresh.sh` (**GCS** pull → pipeline → push → `/reload` — it does NOT `git pull`; the box builds whatever is checked out, so deploys to sparky are a manual `git pull`), log `~/insider-intel/logs/spark_refresh.log`. Enrichment caps rescaled for daily cadence 2026-08-22: `SUMMARIZER_MAX_ARTICLES_PER_RUN=160`, `RESERVE=60` (40/30 was 4×/day sizing). Real run 1 (19:44Z–21:36Z, watched) passed all gates; first unattended cycle 2026-08-17 00:00→00:31:51Z verified end-to-end. Cloud Scheduler `corpus-refresh-schedule` **paused** ~19:45Z, kept as rollback (`crontab -r` on sparky, resume scheduler, optionally execute `corpus-refresh` once). Full ops log: [`spark-cutover-handoff.md`](spark-cutover-handoff.md). |
| **Corpus** | 2026-08-17 snapshot (**stale**): 7,193 rows / 1,839 enriched / 540 insider cases. The 2026-08-22 gate replay measured **7,283 filings-channel rows** and **896 adjudicated-insider filings** — the corpus has grown well past the snapshot; take fresh counts from the next cycle before citing any. Writes land daily (one generation per refresh on `processed/articles.jsonl`). |
| **Enrichment** | **ON, Spark-local since 2026-08-16**: chain `SUMMARIZER_LLM_PROVIDER=sparky` only (vLLM, `model: auto` — serves whatever SKU is loaded; a Nemotron model as of 2026-08-22 per operator, docs previously said Qwen; timeout 900s) — **$0 LLM spend**. Caps: `SUMMARIZER_MAX_ARTICLES_PER_RUN=160`, `RESERVE=60` since the daily-cadence rescale 2026-08-22 (40/30 under 4×/day; cloud trickle was 25/15, Haiku, ≈$1–2/day). Known-accepted: hunt synthesis fails under Qwen thinking mode (`OPENAI_COMPAT_ENABLE_THINKING` knob merged, activation gated on a gold-set A/B); occasional Qwen JSON parse failures (guided-JSON queued). Spend gates live (see CLAUDE.md) — thread #10's filings-gate leak still matters for slot waste, not dollars. |
| **Write/ops auth** | **`ADMIN_API_TOKEN` gate LIVE** on `/reload`, subscription writes, both `ingest_url` endpoints. Secret mapped to service (verify) + job (call); per-secret IAM granted to `api-runtime` and `ingest-job`. UI sends it via Settings → OPERATOR TOKEN (localStorage). Deploy smoke ASSERTS unauthenticated writes 401. |
| **Cold-start UX** | **Snapshot-first boot LIVE** (2026-08-06): `pages.yml` builds `web/data/` (slim 200-row snapshot, never committed) into the Pages artifact daily (08:40 UTC, ~40min after the refresh); UI paints ~3s under a CACHED badge, `probeLiveApi` backs off ~75s, flips LIVE on `/health`. deploy-pages poll timeout 20min (backend observed slow). |
| **Job memory** | **4Gi asserted in `deploy-api.yml`** (2026-08-10): the first `force_reprocess` run was OOM-killed (exit 137) mid full-corpus pass — a forced retag holds the whole corpus + graph at once, unlike incremental runs. |
| **Service memory** | **2Gi asserted in `deploy-api.yml`** after the 2026-07-26 OOM burst-503 outage at legacy 1Gi. Must grow with the corpus. |
| **Secrets** | Six mappings **re-asserted with `--update-secrets` on every deploy** (self-healing). **NEVER run manual `--set-secrets`** — it replaces the whole set (caused the 2-day July outage). Audit with `corpus-status`. |
| **ITM** | **v2.11.0** (562 techniques; picked up with the description-clamp fix, 2026-08-08). `itm-refresh.yml` re-pulls weekly and opens a PR when upstream changed (merge = approval; crosswalk guard test catches renumberings — DT067→DT152 already handled). Technique descriptions now clamp at 900 chars on sentence boundaries (was 320, mid-word). |
| **Analytics** | **DIY, daily** (`traffic-report.yml`, 13:00 UTC): forensic per-request CSV with DB-IP geolocation + summary report; run artifacts (download on the run page) + `gs://…/export/traffic-{report.md,log.csv}`. ~13 visits/day; `/evidence/ledger` loads on nearly every visit; scanner probes (`/.env` etc.) all 404/gated. |
| **Hunt synthesis** | NEW 2026-08-08: refresh job distills each observed technique’s case material into tool-agnostic detect/prevent hunt patterns (telemetry + process + people) (`data/state/technique_hunts.json`, signature-cached, `HUNT_SYNTH_MAX_PER_RUN=10`, chain = summarizer chain/Haiku). Dossier leads with patterns; entity terms (names/companies/domains) are filtered from all hunt surfaces. Initial sweep fills over ~4 days of refreshes. MODUS OPERANDI slimmed to a forensic case study (2026-08-09): SIEM query/seed surfaces removed from report + export + LLM prompt; hunting guidance cross-links to the dossier patterns. |
| **PACER purchasing** | ARMED (`PACER_PURCHASE_MAX_PER_RUN=5`, $27/quarter cap under the fee waiver). Creds moved into `.env.spark` on sparky at the 2026-08-16 cutover. |
| **CourtListener** | Paid Tier-2 token; delay 5s; history sweep at floor (2015-01-01 reached — sweeps complete each run). |
| **UI redesign (2026-08-10)** | Claude Design redesign ported: no intro panel; masthead corpus-stats line + status band (lanes, UTC clock); provenance meta lines (SOURCE · FILED · RETRIEVED · SIG · proof); plain-language proof standard (CONFIRMED IN COURT / ALLEGED / REPORTED, from forensics claim_status); stream = content + one right rail (techniques tally + ledger), board on WORKBENCH takeover only; footer with methodology/about pane + neutral theme labels. Redesign source in `design/redesign/`. |
| **UI honesty** | Settings is reader-safe (no TODO / stub ADD / Notifications chrome). Empty board offers **TRY EXAMPLE HUNT**. Default theme **Wire Light** (cnn-lite; was Dossier Sage until 2026-08-11); desktop rail JS breakpoint matches CSS at **1024px**. |

---

## Recent sessions' changes (PRs on `main`)

| PR | What / why |
|---|---|
| #130 | Feedly/td3.dev doc scrub + mobile case-meta wrap fix |
| #131 | `corpus-noninsider` diagnostic (spend-waste audit) |
| #132 | **Spend gates** (filings in-body signal, news technique-hit) + CONTEXT stream filter |
| #133 | `service-logs` diagnostic (Cloud Run API errors + 5xx) |
| #134 | **2Gi memory** (OOM burst-503 outage fix) |
| #135 | CLAUDE.md brought current (gates, EVIDENCE, diagnostics, gotchas) |
| #136 | README rewrite (novel-technique-discovery mission) + **ADMIN_API_TOKEN gate** |
| #137 | **ITM 2.9** (+31 techniques, DT067→DT152) + weekly auto-refresh |
| #138 | Smoke accepts 401 on gated ingest_url probe |
| #139 | **Trickle-mode caps** (park spend-safe) |
| #140 | **Snapshot-first boot** (CACHED→LIVE, web/data via Pages artifact) |
| #141 | deploy-pages 20min timeout |
| #142–#145 | DIY traffic analytics: weekly→**daily**, forensic CSV + GeoIP (+ column fix) |
| #188 | **Spark corpus-tenant mode** (compose + `spark_refresh.sh` + timeout knob). Cutover **DONE 2026-08-16** — see thread #5. |
| #189 | Stamp `forensics.model` from served OpenAI-compat id; `model: auto` (merged; live in the Spark tenant) |
| #190 | `spark_refresh.sh` wrapper hardening (post-incident: flock, cycle bound, `--build`, reload skip when `SPARK_RELOAD_URL` empty) |
| #196–#197 | **octoDNS: thederpweb.com zone live on Cloudflare** (zone reconciled; null MX + SPF `-all` + DMARC reject added; `insider-intel.net` apex/www stay Worker-owned behind a `NameRejectlistFilter`) — see thread #13 |

---

## Operational knobs & where they live

- **Models / caps / pacing / PACER cap / memory / secret mappings** →
  `.github/workflows/deploy-api.yml`. **GitOps: edit + merge**, never gcloud.
  Every deploy re-stamps job env (`--update-env-vars`, merge semantics) and
  re-asserts all six secret mappings (`--update-secrets`).
- **Secret VALUES** → Secret Manager only (operator-manual, one-time; plus
  per-secret `secretAccessor` grants to `api-runtime` and `ingest-job` SAs).
- **Read-only diagnostic + export workflows** (Actions → Run workflow):
  `corpus-status` (state + env audit), `corpus-count`, `corpus-sample`,
  `corpus-noninsider` (spend-waste audit), `courtlistener-worklist`,
  `evidence-ledger`, `export-llm`, `service-logs`, `probe-extract`,
  `traffic-report`, `refresh-corpus`/`watch-refresh`, `reenrich-drain`,
  `corpus-recover`. A dispatch workflow must exist on `main`; it then runs
  the file from any ref (branch diagnostics without merging).

---

## Open threads

1. **In-repo settings + lexicon config** — decided direction unchanged
   (checked-in `config/app_config.json`, fallback to defaults, secrets stay
   in Secret Manager). Not built.
2. **Collect-only vs enrich** — superseded by trickle mode (#139): enrichment
   stays on at ~$1–2/day. `SUMMARIZER_MAX_ARTICLES_PER_RUN=0` remains the
   off switch.
3. **PACER activation** — DONE 2026-07-24.
4. **Discovery lanes** — (a) FLP tech-cases-bot feed; (b) ITM-derived query
   generator. Not built.
5. **Spark as the corpus tenant — DONE 2026-08-16 (cutover complete).**
   Architecture shipped (#188, hardened by #190, `model: auto` via #189);
   sparky is the production refresh tenant: cron daily (see Live state row),
   sparky-only Qwen chain ($0 LLM spend), caps 40/30, PACER creds on the
   box, first unattended cycle verified 2026-08-17 00:31Z. Cloud Scheduler
   `corpus-refresh-schedule` paused as rollback. Full record + operating
   state: [`docs/spark-cutover-handoff.md`](spark-cutover-handoff.md) and
   `docs/dgx-spark.md` §4. Remaining follow-ups: thinking-mode knob
   activation (gold-set A/B), guided-JSON for Qwen parse failures, vLLM key
   rotation at next restart, sparky-repo doc de-pin, Reddit OAuth.
6. **UI feed auto-discovery** — `<link rel="alternate">` for `/feed.xml`
   still a one-line deferred change.
7. **Cold-start UX** — **DONE 2026-08-06** (#140/#141): snapshot-first boot
   shipped (variant (a) of the old plan). Variant (b) (cpu-boost + prebuilt
   index) remains optional if wake time itself ever matters.
8. **International court-filings lanes** — phase 0 (prosecutor/regulator
   feeds) + phase-2-lite (CanLII RSS) SHIPPED; **IndiaCourts lane BUILT
   2026-08-22 on branch `claude/indian-court-judgment-eval-3aa51n`
   (UNMERGED — operator review pending)**: $0 lexicon-scan lane over the CC
   BY 4.0 eCourts open dataset (all 25 HCs, floor 2000, hub-first walk,
   dark behind `INDIACOURTS_ENABLED`), with legal provenance
   (`legal_metadata` + `resolve_country`), Indian legal postures weighted
   below the adjudicated floor, a `country` facet end to end, EVIDENCE
   nation tabs + TACTICS BY REGION, and export schema v6. Requirements:
   docs/india-courts-requirements.md; lane doc: docs/india-courts-ingest.md.
   Activation = enable on sparky + optional OCR command (bench first).
   **phase 1 UK Find Case Law** pipeline module still unbuilt — the new
   jurisdiction plumbing serves it. CanLII API stays a no-go
   (metadata-only). AU direct / EU national courts not chosen.
9. **EVIDENCE flagship** — P1 + P2-findings SHIPPED (live at → EVIDENCE,
   `web/findings.json` publish-by-merge). Findings F2–F4 added 2026-08-09
   (proven-vs-alleged split, email-as-winning-evidence, third-party proven
   share), written for business decision makers; F1 restated to the same
   ledger run; findings block now renders at the top of the EVIDENCE page.
   Remaining: CISA/NITTF maturity
   crosswalk (P2), dwell-time from forensics.timeline + static crawlable
   export + OG meta (P3), "departing" employment-state extractor-prompt
   nudge (known under-capture: 7 of 320).
   **OPERATOR FINDINGS SEED (verbatim thesis, captured 2026-07-25):**
   "We analyzed insider threats across many court cases. We found
   executives to be the largest insider threat. So why are we so afraid of
   investigating our executives? Well, because they sign our paychecks.
   Our biggest threat are those above us, but we often don't wanna do
   anything about it. So how do you respond? How do you send the signal
   when it's to those you report to? This is the data. What do we do
   about it?"
   Published as finding F1 with the selection-bias-is-the-finding framing;
   data: executive/officer 230/320 role-known (47%), 26 adjudicated;
   external record classes (Form 4 ×36, public-vs-internal ×83, brokerage)
   make the cases — detecting upward needs no permission to surveil upward.
10. **Filings spend-gate leak — FIX BUILT 2026-08-22 on branch
    `claude/indian-court-judgment-eval-3aa51n` (UNMERGED — dry-run review
    pending).** 2026-08-04 audit: of 553 post-gate enrichments, **58% still
    adjudicated non-insider** — the in-body ITM alias check passed
    company-v-company IP/trade-secret litigation. The branch implements the
    proposed fix: two-part body signal (ITM alias AND
    `INSIDER_FRAMING_KEYWORDS` hit), match-marker stripping, and the
    `FILINGS_SOURCE_PREFIXES` channel fix (canlii-/indiacourts- were
    news-gated). **Replay run on the live corpus 2026-08-22**: 7,283 filings
    rows; old gate bills 2,295 → new 2,011; savings 206 adjudicated
    non-insider; false negatives 51/896 adjudicated-insider filings (5.7%),
    every one with zero framing keywords after marker strip. FN alias
    histogram: "public statement" ×23 (IF012), "insider trading" ×20
    (IF016.004), "aiding and abetting" ×6 (ME018), embezzlement/
    misappropriation ×3 (IF016), no alias ×6. Tuned per operator directive
    (follow ITM): `STRONG_INSIDER_OFFENSES` = insider trading (IF016.004) +
    embezzlement (IF016) — ITM **infringements** only — pass the body
    signal alone when named **≥3×** after statute-title/policy boilerplate
    excision (`STRONG_OFFENSE_BOILERPLATE`). The mention floor exists
    because two `DEFAULT_QUERIES` are the bare phrases themselves — every
    row those lanes admit contains its phrase by construction, so
    phrase-alone degenerated them to a body-length gate (2026-08-22
    adversarial review, executed counterexamples: 10b-5 statute-title
    citations, ERISA/Dudenhoeffer quotes, D&O disputes). "Economic
    espionage" was tried and REMOVED (ITM has espionage only as motive
    MT017/MT005.x; §1831 external-APT indictments false-billed; zero FN
    recovery). "Public statement", "aiding and abetting", bare
    "misappropriation" stay blocked (the 58%-leak vocabulary); strong
    phrases stay OUT of the framing list — there one phrase would satisfy
    both halves. Expected recapture ~20–23 of 51 (FN → ~3%). The replay
    script now prints a **strong-offense-only** section (these rows never
    show as old→new transitions — the old alias-only gate billed them
    too); judge the tune there and by SAVINGS vs the 206 baseline.
    **Remaining review gate:** re-run the replay on the branch and confirm
    the securities cluster is recovered before merging #247. Blocked rows
    were CourtListener-only; existing enrichments are untouched (future
    spend only).
11. **Admin page** — direction discussed, not decided: Cloudflare Access in
    front of an `/admin` route (free, auth at edge) vs Google IAP (needs LB,
    ~$18/mo). Gating question: is thederpweb.com DNS on Cloudflare? —
    **answered YES 2026-08-16** (thread #13), so Cloudflare Access is now
    unblocked. A public
    unauthenticated admin subdomain was rejected (adds scan surface).
12. **Misc parked**: EXPORT CASES full-filing-text plan (unbuilt);
    README byline + LICENSE (operator undecided); landscape-iPhone smoke
    failure (pre-existing); one-off
    04:00Z 2026-08-07 refresh miss (self-healed — watch for recurrence).
    Notifications delivery backend still unbuilt (UI stub removed 2026-08-10
    so Settings stays honest until it exists).
13. **Prod domain + Cloudflare migration (ACTIVE 2026-08-11).** Plan:
    register `insider-intel.net` on Cloudflare Registrar (DNS check says
    likely available — no NS delegation; .com/.io variants taken), park it
    3–6 months to age past newly-registered-domain filters, then cut prod
    over; `intel.thederpweb.com` becomes dev. thederpweb.com transfers off
    Route 53 to Cloudflare (remaining years + creation date carry over,
    +1yr on transfer). Tooling SHIPPED: octoDNS scaffold under `dns/`
    (`dns-plan` on PR/dispatch, `dns-apply` on merge; `CLOUDFLARE_API_TOKEN`
    repo secret — Zone:Read + DNS:Edit, all zones — is set). Zones stay
    `{}` in `dns/config.yaml` until they exist on Cloudflare; activation
    checklist in `dns/README.md`. Also unblocks thread #11's Cloudflare
    Access.
    **Status 2026-08-16:** `insider-intel.net` **registered** (2026-08-11)
    and **serving the site** via the park-redirect Worker; its apex/www DNS
    are Worker-owned and guarded by a `NameRejectlistFilter` in the octoDNS
    config. thederpweb.com **nameservers flipped to Cloudflare**
    (lina/matteo) 21:00Z via the Route 53 Domains API, after octoDNS
    reconciled the zone (PRs #196/#197; null MX + SPF `-all` + DMARC reject
    added). Registrar transfer to Cloudflare **initiated 21:06Z**,
    auto-completes by **2026-08-21**. Route 53 hosted zone retained as
    rollback. Remaining: transfer auto-completion, then the .net aging
    clock before the prod cutover.
    **BUG FIX QUEUED (operator, 2026-08-22): move the API onto
    insider-intel.net** — today the API lives at `api.intel.thederpweb.com`
    (Cloud Run domain mapping; `web/config.js` points every public host at
    it) while insider-intel.net serves only the park-redirect Worker with
    its apex/www guarded by the octoDNS `NameRejectlistFilter`. Scheduled
    **after the India-lane deploy** (operator ordering). Scope: add
    `api.insider-intel.net` DNS via the `dns/**` merge-audited flow (lift or
    scope the rejectlist for the `api` name), create the Cloud Run domain
    mapping, wait out cert propagation (see gotchas — verify with repeated
    curls), update `web/config.js` + API `CORS_ORIGINS`, keep the old
    hostname serving as an alias until cutover confidence.

---

## Conventions

- **Docs ride the same PR.** If a change alters architecture, invariants,
  ops knobs, or live state: update CLAUDE.md and/or this file in that PR —
  never "later". This file's **Last updated** date is the freshness tripwire.
- The PR template (`.github/pull_request_template.md`) carries the checklist.

## Environment note for the assistant

The build/agent sandbox **cannot reach GCP or the prod API**. Do infra changes
through **GitOps (edit `deploy-api.yml` + merge)** or **read-only OIDC
workflows** dispatched from the Actions tab (or the GitHub API). Secret
*values* and IAM grants stay operator-manual.

## Verification (all local — no GCP)

```bash
make test lint fmt          # same targets CI runs; green local == green CI
# or: ruff check apps shared tests && pytest -q
PYTHONPATH=. python scripts/ui_smoke.py   # 52/53 baseline (landscape fail pre-existing)
```
