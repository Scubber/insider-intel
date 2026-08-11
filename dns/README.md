# DNS as code (octoDNS → Cloudflare)

Zones in this directory are the declared state; `dns-plan.yml` shows the
diff on every PR that touches `dns/**` (and on manual dispatch, which also
smoke-tests the API token), and `dns-apply.yml` pushes merged changes to
Cloudflare. Records ship by PR like every other change in this repo.

Status: **scaffold** — no zone is active yet (`zones: {}` in config.yaml).

Activation checklist, per zone:
1. Zone exists in the Cloudflare account (insider-intel.net registered
   there; thederpweb.com transferred in / added + delegated).
2. Real records committed to the zone's YAML (for thederpweb.com, dumped
   from Route 53 — never hand-authored from memory).
3. Zone uncommented in `config.yaml`.
4. PR → check the plan in the dns-plan run → merge.

Domain-age note: insider-intel.net stays parked until it is 3–6 months
old — corporate newly-registered-domain filters (the audience's own
tooling) block younger namespaces. intel.thederpweb.com remains the
serving domain until cutover; afterwards it becomes the dev site.
