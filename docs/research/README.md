# Research briefings — parked drafts

**Status: PARKED 2026-09-05 (operator decision).** The site has no RESEARCH
tab, pane, or route. The briefings stay here as drafts. Rewrite each one
before it goes public again.

## What these are

Long-form findings written from the corpus. Each briefing is a dated
publication: the numbers were read from the corpus on its AS OF date and
frozen there. That is deliberate. Everything else on the site recomputes
from the live corpus; a briefing is the one place a frozen number is honest,
because the dateline says exactly when it was true and the text points at
EVIDENCE for the live figures.

Rules that still apply to a draft:

- The dateline carries `PUBLISHED` and `AS OF` with the corpus basis.
- A `Limits` section says what the record cannot show.
- Roles, never individuals. Adjudicated, alleged, and reported are never
  mixed.
- Voice: short plain sentences, a briefing memo, no marketing adjectives.

## Files

`drafts/<slug>.html` — one file per briefing. Each starts with an HTML
comment (slug, original dateline, the PARKED stamp, the index-card title and
summary that sat on the RESEARCH index page), followed by the
`<article class="research-briefing" data-briefing="<slug>">` block exactly
as it shipped. Paste-ready.

| Slug | Title |
|---|---|
| `fs-insider-profiles-2026-10` | Who the insiders are in financial-services cases — edition 2 |
| `fs-insider-profiles-2026-09` | Who the insiders are in financial-services cases |
| `danger-profiles-2026-08` | Who the most dangerous insiders are |
| `email-destinations-2026-08` | Where insider email actually goes |

`tests/test_research_page.py` pins this parking: no RESEARCH markup or
route opener in `web/`, and every draft here keeps its header, dateline,
and Limits section.

## How to republish one

The page, tab, routes, and styles were removed in one change on
2026-09-05 (branch `claude/evidence-page-redesign-awjijy`). To bring a
briefing back:

1. Restore the RESEARCH pane from that change's diff: the masthead
   button and mobile tab (`data-pane="research"`), the GUIDE
   `<dt>RESEARCH</dt>` line, the `<section class="pane pane-research-page">`
   with its index, `openResearchView` and the `/research` routes in
   `web/app.js`, and the `.research-*` rules in `web/styles.css`.
2. Paste the rewritten `<article>` (add `hidden` back) into the pane and
   add its index card (title, dateline, summary from the file header).
3. Add the slug to `tests/test_research_page.py::SLUGS` and to both
   deep-link lists in `scripts/ui_smoke_ci.py`.
4. Update the AS OF dateline if the numbers were re-read from the corpus.
5. Run `pytest`, `scripts/ui_smoke_ci.py`, and screenshot at 390, 768,
   1024, and 1280 before merge.
