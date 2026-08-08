## What / why

<!-- One or two sentences: the change and the reason. -->

## Docs

<!-- Docs ride the same PR (CLAUDE.md convention). Check what applies: -->

- [ ] `CLAUDE.md` updated (architecture / invariants / gotchas / diagnostics)
- [ ] `docs/HANDOFF.md` updated (live state / open threads / Last-updated date)
- [ ] `README.md` updated (public-facing behavior)
- [ ] No docs impact — because: <!-- say why -->

## Verification

<!-- How this was checked. For web/** changes: Playwright drive/screenshot
     (incl. the responsive widths touched — phone/iPad/desktop) + ui_smoke.
     For code: pytest + ruff. For workflows: dry-run or dispatch. Say what
     you skipped and why. -->
- [ ] UI change: Playwright drive/screenshot at the affected widths + `ui_smoke`
- [ ] Code change: `pytest` + `ruff`
- [ ] Workflow change: dry-run or dispatched
- [ ] N/A — because: <!-- say why -->

