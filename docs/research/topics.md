# Research briefing queue

The scheduled research briefing (see [routine-prompt.md](routine-prompt.md))
takes the first `queued` topic from this table each run, writes a briefing
from a `research-pack` pull plus outside sources, emails it to the operator,
and opens a PR that moves the row to `drafted` with the draft file beside it.
Topics are AUTHORED here (a slug, a question, the pack spec); the numbers
never are — a briefing's figures live only in its dated draft.

Add topics anywhere in the table. Order is priority. `pack` is the
`research-pack` dispatch input set; a second spec in the same cell means
the briefing compares two slices.

| Status | Slug | Question the briefing answers | Pack spec | Seed / notes |
|---|---|---|---|---|
| queued | `court-funnel-2026-10` | Why so few insider cases reach a verdict: what the posture funnel (reported → alleged → adjudicated) looks like in the record, and what stops a case short. | `query="trade secret\|misappropriat\|confidential information\|proprietary"` · compare `posture=adjudicated` vs `posture=alleged` | Seed: an operator post, 2026-10 — "Trade secret theft costs 1–3% of GDP yet ~1,500 federal cases a year vs 5–6k patent cases. Why? Owners don't know what they have; plaintiffs can't afford it; people think taking data is normal; no 'reasonable measures' taken." Test each reason against the record. |
| queued | `notice-window-exfil` | The last thirty days: what departing employees take, through which channel, and how it was found. | `query="resign\|notice period\|last day\|two weeks\|terminat\|departure"` | Compare exfil channels and detection families against the all-cases pack (`query=.`). |
| queued | `how-they-got-caught` | Who noticed first: colleague, audit, forensic exam, monitoring alert, customer — and what that says about where detection budget goes. | `query=.` | Detection families are regex over the enricher's prose; say so. Tie to TOOLING categories. |
| queued | `controls-that-held` | Tools named in court as having caught, been bypassed, misused or traced the insider: which control classes hold up in a filing. | `query=.` · also `posture=adjudicated` | v3 `tool_mentions` only; vendor names allowed as case mentions, never as effectiveness claims. |
| queued | `personal-email-exfil` | Email to a personal account as the exfil channel: how common, what was sent, what caught it. | `query="personal email\|personal account\|gmail\|yahoo\|forward(ed)? to"` | `corpus-emailscan` export carries the domain tally; cite it. Never a local part. |
| queued | `usb-and-cloud` | Removable media versus cloud sync: which channel dominates, and whether that changed by year. | `query="usb\|thumb drive\|flash drive\|external (hard )?drive\|dropbox\|google drive\|onedrive\|icloud\|box\.com"` | Year table is in the pack; compare pre/post 2020. |
| queued | `reasonable-measures` | What courts said about "reasonable measures": which protections (NDAs, access controls, marking, offboarding) the record treats as adequate or missing. | `query="reasonable measures\|reasonable efforts\|confidentiality agreement\|non-disclosure\|NDA\|access control"` | Legal-element framing; keep to what filings say, no legal advice. |
| queued | `insider-trading-tipping` | Tipping chains: how insider-trading cases describe the first leak, the chain, and the signal that surfaced it. | `techniques=IF016` · `query="tip\|tipp\|material non-?public\|MNPI"` | ITM IF016.004. Securities cases dominate the adjudicated tier; say why. |
| queued | `moonlighting-overemployment` | Moonlighting and dual employment: what filings show versus what first-person confessions on social admit. | `techniques=IF038` · compare channel counts in the pack | Social rows are reported/unclear by construction. |
| queued | `contractors-and-vendors` | Third-party insiders: contractors, vendors and consultants in the record, and how their access differed. | `query="contractor\|subcontract\|vendor\|consultant\|third.party\|staffing"` | Access vector table is the lead. |
| queued | `india-vs-us` | Jurisdiction contrast: what Indian High Court filings show about insiders that US dockets do not, and the reverse. | `country=IN` vs `country=US`, `query=.` | Indian postures (FIR, charge sheet, bail, quashing) sit below the adjudicated floor; never compare them as if equal. |
| queued | `negligent-insiders` | Mistakes versus malice: how the record treats negligent and unintentional insiders, and what detected them. | `query="negligen\|inadvertent\|accidental\|mistaken\|misdirected\|misconfigur"` | Classifier `insider_type` counts are in the pack; label them as classifier output. |
| queued | `customer-lists` | Customer lists as the most litigated secret: which roles take them, where they go, and how the loss surfaces. | `query="customer list\|client list\|book of business\|customer data\|contact list"` | Sales roles expected to dominate; check the role table before claiming it. |
| queued | `remote-work-shift` | Did the channels insiders use change after 2020? Year-by-year exfil channel and detection family mix. | `query=.` with `year_from=2015` | Compare the year table and channel counts; filing-year lag is a limit. |
