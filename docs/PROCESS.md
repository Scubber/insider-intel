# Process — Kanban + milestone gates

Not Scrum. Continuous delivery on `main` with explicit gates.

## Kanban (WIP)

| Column | Meaning | WIP |
|--------|---------|-----|
| Backlog | Ordered next work (GitHub issues) | — |
| Doing | In progress | **2** (max 1 feature + 1 host/ops) |
| Review | PR open / local verify / CI | 2 |
| Done | Merged to `main` or gate closed | — |

Prefer finishing **M2 Hosted MVP** cards before new feature cards until that milestone closes.

Track work as **GitHub Issues** on the active milestone. A Projects board can mirror the same columns once the `project` token scope is available (`gh auth refresh -s read:project,project`).

## Milestone gates

| Milestone | Exit criteria | Tag / status |
|-----------|---------------|--------------|
| **M1 — Prototype candidate** | On `main`; CI green; Pages UI; honest about bake/redeploy host | `mvp-prototype-0.1` |
| **M2 — Hosted MVP** | GCS corpus; scheduled ingest; live `api.intel.thederpweb.com`; CD path | `v0.2.0` · Hosted MVP |
| **M3 — Reliable ops** | Monitoring, secret rotation, settled scale — later backlog | — |

Links: [M1](https://github.com/Scubber/insider-intel/milestone/1) · [M2](https://github.com/Scubber/insider-intel/milestone/2)
