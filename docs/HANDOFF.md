# HANDOFF — current state for the next session / IDE / LLM

**Read this to pick up where the last session left off.** It is the live
operational state; [`../CLAUDE.md`](../CLAUDE.md) is the architecture/operating
manual, [`hosting.md`](hosting.md) the production detail, and the merged PRs
(linked below) are the diff-level changelog.

**Last updated:** 2026-08-10 · **Repo:** `Scubber/insider-intel` · **Prod:**
API on Cloud Run (`insider-intel-api`, 2Gi), UI on GitHub Pages
(`intel.thederpweb.com`), corpus in GCS, corpus-refresh job every 6h.
**Rollback checkpoint:** branch `checkpoint/v1.0-parked` (pre-August prod).

---

## Live production state

| Area | State |
|---|---|
| **Corpus** | ≈ **7,000 rows**, **1,477 enriched** (forensics present), **540 LLM-adjudicated insider cases**. Writes land 4×/day (~04:1x/10:1x/16:1x/22:1x UTC generations on `processed/articles.jsonl`). |
| **Enrichment** | **ON, trickle mode** (2026-07-26): `SUMMARIZER_MAX_ARTICLES_PER_RUN=25`, `RESERVE=15`, `DISCOVERER=3`, `COURTLISTENER_BACKFILL_MAX_DOCKETS=10`. Haiku 4.5. Spend ≈ $1–2/day worst case. Spend gates live (see CLAUDE.md) — but see open thread #10: the filings gate leaks. |
| **Write/ops auth** | **`ADMIN_API_TOKEN` gate LIVE** on `/reload`, subscription writes, both `ingest_url` endpoints. Secret mapped to service (verify) + job (call); per-secret IAM granted to `api-runtime` and `ingest-job`. UI sends it via Settings → OPERATOR TOKEN (localStorage). Deploy smoke ASSERTS unauthenticated writes 401. |
| **Cold-start UX** | **Snapshot-first boot LIVE** (2026-08-06): `pages.yml` builds `web/data/` (slim 200-row snapshot, never committed) into the Pages artifact on a 6h schedule; UI paints ~3s under a CACHED badge, `probeLiveApi` backs off ~75s, flips LIVE on `/health`. deploy-pages poll timeout 20min (backend observed slow). |
| **Service memory** | **2Gi asserted in `deploy-api.yml`** after the 2026-07-26 OOM burst-503 outage at legacy 1Gi. Must grow with the corpus. |
| **Secrets** | Six mappings **re-asserted with `--update-secrets` on every deploy** (self-healing). **NEVER run manual `--set-secrets`** — it replaces the whole set (caused the 2-day July outage). Audit with `corpus-status`. |
| **ITM** | **v2.11.0** (562 techniques; picked up with the description-clamp fix, 2026-08-08). `itm-refresh.yml` re-pulls weekly and opens a PR when upstream changed (merge = approval; crosswalk guard test catches renumberings — DT067→DT152 already handled). Technique descriptions now clamp at 900 chars on sentence boundaries (was 320, mid-word). |
| **Analytics** | **DIY, daily** (`traffic-report.yml`, 13:00 UTC): forensic per-request CSV with DB-IP geolocation + summary report; run artifacts (download on the run page) + `gs://…/export/traffic-{report.md,log.csv}`. ~13 visits/day; `/evidence/ledger` loads on nearly every visit; scanner probes (`/.env` etc.) all 404/gated. |
| **Hunt synthesis** | NEW 2026-08-08: refresh job distills each observed technique’s case material into tool-agnostic detect/prevent hunt patterns (telemetry + process + people) (`data/state/technique_hunts.json`, signature-cached, `HUNT_SYNTH_MAX_PER_RUN=10`, chain = summarizer chain/Haiku). Dossier leads with patterns; entity terms (names/companies/domains) are filtered from all hunt surfaces. Initial sweep fills over ~4 days of refreshes. MODUS OPERANDI slimmed to a forensic case study (2026-08-09): SIEM query/seed surfaces removed from report + export + LLM prompt; hunting guidance cross-links to the dossier patterns. |
| **PACER purchasing** | ARMED (`PACER_PURCHASE_MAX_PER_RUN=5`, $27/quarter cap under the fee waiver). |
| **CourtListener** | Paid Tier-2 token; delay 5s; history sweep at floor (2015-01-01 reached — sweeps complete each run). |
| **UI honesty** | Settings is reader-safe (no TODO / stub ADD / Notifications chrome). Empty board offers **TRY EXAMPLE HUNT**. Default theme aligned to **Dossier Sage**; desktop rail JS breakpoint matches CSS at **1024px**. |

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
5. **Off-site LLM enrichment** — `export-llm` remains the delivery lane.
6. **UI feed auto-discovery** — `<link rel="alternate">` for `/feed.xml`
   still a one-line deferred change.
7. **Cold-start UX** — **DONE 2026-08-06** (#140/#141): snapshot-first boot
   shipped (variant (a) of the old plan). Variant (b) (cpu-boost + prebuilt
   index) remains optional if wake time itself ever matters.
8. **International court-filings lanes** — phase 0 (prosecutor/regulator
   feeds) + phase-2-lite (CanLII RSS) SHIPPED; **phase 1 UK Find Case Law**
   pipeline module still the real build. CanLII API stays a no-go
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
10. **Filings spend-gate leak (ACTIVE — next real work item).** 2026-08-04
    audit: of 553 post-gate enrichments, **58% still adjudicated
    non-insider** — the in-body ITM alias check passes company-v-company
    IP/trade-secret litigation and CanLII judgments with zero methods
    (news gate worked: only +18 in 9 days). Proposed fix: filings body must
    also contain an `INSIDER_FRAMING_KEYWORDS` hit ("former employee" etc.),
    with a dry-run replaying the gate over the 937 stored non-insider vs
    540 insider rows to measure the false-negative cost first.
11. **Admin page** — direction discussed, not decided: Cloudflare Access in
    front of an `/admin` route (free, auth at edge) vs Google IAP (needs LB,
    ~$18/mo). Gating question: is thederpweb.com DNS on Cloudflare? A public
    unauthenticated admin subdomain was rejected (adds scan surface).
12. **Misc parked**: EXPORT CASES full-filing-text plan (unbuilt);
    README byline + LICENSE (operator undecided); landscape-iPhone smoke
    failure (pre-existing); one-off
    04:00Z 2026-08-07 refresh miss (self-healed — watch for recurrence).
    Notifications delivery backend still unbuilt (UI stub removed 2026-08-10
    so Settings stays honest until it exists).

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
