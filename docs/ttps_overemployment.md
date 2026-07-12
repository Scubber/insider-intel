# Overemployment TTPs (IF038) — investigator hunt cues

**ITM:** [IF038 — Undisclosed Concurrent Employment](https://insiderthreatmatrix.org/)  
**Hunt meaning here:** human investigation across **email, chat, network, and HR/legal** — not SOC-only.

## MVP scope note

CourtListener RECAP search currently lands **docket metadata** (case name, court, cause, parties), not full opinion text. Deep “LLM read every opinion” is **later**. This seed list is grounded in:

1. **Case types** already pulled into the local corpus (trade-secret / employment disputes co-occurring with dual/outside employment queries).
2. **Investigator-standard MOs** for undisclosed concurrent remote employment (Overemployed-style / dual W-2 patterns) that operators actually search for.

Revisit with CourtListener MCP + opinion text when OAuth is connected; promote findings into aliases and this doc.

### Example docket types in corpus (illustrative, not proven facts of each case)

| Pattern | Example links (RECAP) |
|--------|------------------------|
| Trade secret + concurrent/dual employment query | [PSG of Sarasota v. Campbell](https://www.courtlistener.com/docket/73352035/psg-of-sarasota-llc-v-campbell/), [Woodbolt v. Thomas](https://www.courtlistener.com/docket/72346154/woodbolt-distribution-llc-v-thomas/), [REV Group v. Knutz](https://www.courtlistener.com/docket/71991112/rev-group-inc-v-knutz/), [Benco Dental v. Goodpasture](https://www.courtlistener.com/docket/72284134/benco-dental-supply-co-v-goodpasture/) |
| Employment / labor + concurrent employment query | [Petrovic v. TCS](https://www.courtlistener.com/docket/72109786/margareta-petrovic-v-tata-consultancy-services-limited/), [Seward v. InHospital Physicians](https://www.courtlistener.com/docket/72052728/seward-v-inhospital-physicians-corp/) |
| Outside employment / policy / disclosure queries | Use Channel **Filings** + Hunt **overemployment** / **outside employment** after reprocess |

Treat each docket as a lead to read — do not assert case facts from metadata alone.

---

## Behaviors → multi-channel hunt cues

### TTP-OE-01 — Undisclosed second full-time remote job

**Behavior:** Employee holds two (or more) full-time roles; Job A is unaware; often remote-enabled.

| Channel | What to look for |
|---------|------------------|
| **Human / HR** | Missing or false outside-employment / COI disclosure; dual W-2 / multiple employers on tax or benefits paperwork; LinkedIn “current” roles conflicting with HRIS title; manager never sees camera-on / hard-to-schedule |
| **Email** | Mail to/from personal domains during work hours; Job B recruiter or HR threads in personal mailbox; calendar invites for Job B interviews/standups on personal calendar forwarded oddly |
| **Chat** | Second Slack/Teams/Discord identity; DMs about “J2” / “OE” / “overemployed”; status always Busy/BRB; rapid context-switching language |
| **Network** | Concurrent SaaS sessions (GitHub/Office/Okta) from same home IP for different orgs (if visible); personal VPN + corp VPN patterns; unusual after-hours bursty productivity tools |

**Paste / search seeds:** `outside employment`, `moonlighting`, `J2`, `overemployed`, `second job`, `dual employment`, `conflict of interest disclosure`

### TTP-OE-02 — Competitor / customer side work (trade-secret adjacent)

**Behavior:** Concurrent role or contracting with a competitor, vendor, or customer while still employed — often appears in DTSA / trade-secret dockets.

| Channel | What to look for |
|---------|------------------|
| **Human / HR** | Undisclosed advisory/board/contractor role; COI form “none”; resignation timed with competitor start |
| **Email** | Attachments or threads with competitor domains; “side project” share of internal decks; personal Dropbox/Drive links in corp mail |
| **Chat** | Sharing screenshots of internal tools; “my other company”; recruiting coworkers |
| **Network** | Large personal-cloud uploads; USB/email exfil adjacent to resignation; access to repos unused in day job |

**Paste / search seeds:** `competitor`, `side project`, `advisory`, `consulting agreement`, `DTSA`, `trade secret`, `customer list`

### TTP-OE-03 — Using Employer A time/tools for Employer B

**Behavior:** Job B work performed on Employer A laptop, VPN, email, or paid hours.

| Channel | What to look for |
|---------|------------------|
| **Human / HR** | Timekeeping anomalies; “always in meetings” with no corp meeting artifacts; PIP for availability |
| **Email** | Drafts to Job B from corp mailbox; calendar blocks labeled vaguely (“focus”, “project”) with no corp attendees |
| **Chat** | Pasting Job B tickets/snippets into corp chat by mistake; second browser profile language |
| **Network** | Non-approved SaaS (Job B IdP) on corp device; RDP/VDI to personal systems; high clipboard/file activity to personal cloud |

**Paste / search seeds:** `personal laptop`, `my other job`, `client call` (no corp attendees), Job-B product/tool names

### TTP-OE-04 — Identity split (personal vs corporate)

**Behavior:** Deliberate separation — personal phone/laptop for Job B; corp stack only for Job A.

| Channel | What to look for |
|---------|------------------|
| **Human / HR** | Employee never reachable on corp mobile; refuses corp MDM on personal devices used for “work” |
| **Email** | Auto-forward corp → personal (policy violation); Job B never appears on corp systems |
| **Chat** | “Text me on my personal”; Signal/WhatsApp for work topics |
| **Network** | MDM gaps; personal hotspot only; corp VPN idle while claiming productive hours |

**Paste / search seeds:** `personal phone`, `text me`, `Signal`, `WhatsApp`, `forward to Gmail`

### TTP-OE-05 — False / incomplete disclosure

**Behavior:** COI / outside-employment form filed as “none” while second role exists; or disclosure of hobby while omitting paid role.

| Channel | What to look for |
|---------|------------------|
| **Human / HR** | Form answers vs LinkedIn/tax/benefits; vendor AP payments to employee’s LLC; 1099s |
| **Email** | Threads with “please sign outside employment policy”; reminders unanswered |
| **Chat** | Jokes about “don’t tell HR”; policy screenshot shares |
| **Network** | Low signal — pair with HRIS |

**Paste / search seeds:** `outside employment policy`, `conflict of interest form`, `disclosure form`, `moonlighting policy`

---

## Operator workflow (MVP)

1. Hunt UI: **Overemployment** (Maps-to IF038) or paste seeds above.
2. Channel **Filings** for RECAP leads; open docket → read complaint/opinion on CourtListener (MCP when connected).
3. Flag with **+** on Articles (or **Add to board** in Workbench) → **Extract TTPs** for a combined multi-channel hunt report.
   - Live API: `POST /extract/ttps` uses indexed text + optional xAI (`XAI_API_KEY`) and CourtListener REST snippets.
   - **Copy agent brief** for Cursor + CourtListener MCP when you need deeper opinion text.
4. Channel **News** for HR/legal OSINT corroboration (HR Dive / Alerts later).
5. Run investigation from the report — email discovery + chat review + VPN/SaaS + HRIS/COI — not SIEM alone.

## Later (not MVP)

- LLM batch over opinion PDFs / MCP-fetched text → per-case structured TTPs  
- Persist `ai_summary` + channel-tagged hunt cues on each filing  
- Workbench UI grouped Email | Chat | Network | Human  
