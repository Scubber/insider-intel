# OSS Scanner enrolment

[anthropics/oss-scanner](https://github.com/anthropics/oss-scanner) is an
Anthropic service that builds an enrolled open-source project in an
isolated VM, audits it with Claude Code **offline**, and emails findings
(with reproduction steps and a proposed patch where it has one) to the
project's contact. Reports are model-generated, not human-reviewed, and are
never published. There is no GitHub Action, CLI, or package: a project
enrols by adding one directory to their repository.

This directory is our side of that contract:

| File | Role |
|---|---|
| `Dockerfile` | The audit image. Repo at `/src`, all deps installed, Chromium for the UI smoke, a synthetic corpus seeded, the test suite run at build time. Never deployed; the root `Dockerfile` is production. |
| `threat_model.md` | What the auditor reads first: where untrusted input enters, what is in and out of scope, how to exercise the code, how we rate severity. |
| `seed_corpus.py` | Eight fictional cases pushed through the real processing graph so the API and UI serve rows with no bucket and no network. |
| `project.yaml` | A draft of the enrolment file. The live copy lives in **their** repo at `projects/insider-intel/project.yaml`; this one keeps the `dockerfile:` / `threat_model:` paths reviewed next to the files they name. |

`tests/test_oss_scanner.py` pins the contract (their validator's rules, the
paths resolving, no operator address in the repo, house voice in the threat
model). CI's `oss-scanner-image` job builds the image and runs the suite,
the API smoke and the browser smoke with `--network none`, which is the
condition the scanner audits under.

## Check it locally, the way the scanner does

```bash
# From the repo root. The scanner deletes the root .dockerignore before it
# builds; do the same from a clean export so docs/ and scripts/ land in /src.
rm -rf /tmp/ii-scan && mkdir /tmp/ii-scan && git archive HEAD | tar -x -C /tmp/ii-scan && rm /tmp/ii-scan/.dockerignore
docker build -f /tmp/ii-scan/.oss-scanner/Dockerfile -t insider-intel-scan /tmp/ii-scan
docker run --rm --network none insider-intel-scan pytest -q
docker run --rm --network none insider-intel-scan python scripts/ui_smoke_ci.py
docker run --rm -it --network none insider-intel-scan bash     # poke around as the auditor would
```

Or clone their repo and run `tools/validate.py` and `tools/check insider-intel`
with `project.yaml` copied to `projects/insider-intel/` — that adds their
tools layer (Claude Code, gdb, strace) on top and drops you into the
finished image with no network.

## Enrol, change, pause, withdraw

1. Pick the contact. Addresses in `project.yaml` are public once merged.
   Use a security alias, never a personal address, and never commit one
   here (the test forbids it).
2. In a fork of `anthropics/oss-scanner`, add
   `projects/insider-intel/project.yaml` with this directory's draft and
   the real `primary_contact`. One project per PR. Run their
   `tools/validate.py` and `tools/check insider-intel`.
3. Open the PR and fill in their checklist; sign their CLA when the bot
   asks (once). Only a core maintainer can enrol the project; they verify
   that before merging.
4. After merge the scanner builds and audits on its own cadence. A build
   failure emails `primary_contact` with the log; findings email the same
   address.
5. To pause: set `disabled: true` over there. To withdraw: remove the
   directory over there. Update the draft here in the same sitting so the
   two never disagree.

The Dockerfile and threat model are read from **this** repo's `main` at
each build, so they change by ordinary PR here, with no PR over there.
