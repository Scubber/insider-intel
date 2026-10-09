# OSS Scanner build — https://github.com/anthropics/oss-scanner
#
# This file is NOT a production image. The root Dockerfile builds Cloud Run
# and the sparky tenant; this one builds the audit image the scanner runs
# Claude Code inside. Contract (tools/check_build.py in that repo):
#   - built with the repository checkout as the context, root .dockerignore
#     REMOVED, so docs/, scripts/, .github/ are all inside the image;
#   - `COPY . /src` + WORKDIR /src is required: the scanner's own layer
#     (Claude Code, gdb, strace…) apt-installs on top and runs as root in /src;
#   - the network is cut after this build. Anything an auditor might run —
#     pytest, the API, the static UI under a real browser — is fetched here;
#   - 45 min, 16 CPU, 64 GB for the build; 2 CPU / 8 GB for the audit.
#
# Check it the way the scanner will (tools/check in their repo does the same):
#   docker build -f .oss-scanner/Dockerfile -t insider-intel-scan .
#   docker run --rm --network none insider-intel-scan pytest -q
#   docker run --rm --network none insider-intel-scan python scripts/ui_smoke_ci.py
# CI's `oss-scanner-image` job runs exactly those three, so a merge can't
# break the enrolment silently (a scanner build failure only emails the
# operator after the fact).
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# The scanner's layer needs apt + curl/git/bash/procps present or installable.
# poppler + tesseract are the binaries apps/aggregator/ocr_pdf.py shells out
# to on the IndiaCourts scanned-PDF path (same packages as the root
# Dockerfile's `spark` stage), so that code path is exercisable offline.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git bash procps \
        poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

# Runtime + dev deps from pyproject.toml, editable so a patch an auditor
# writes under /src applies without a reinstall. shellcheck is what ci.yml's
# `shell` job runs over scripts/*.sh (unattended production plumbing).
RUN pip install -e ".[dev]" \
    && apt-get update \
    && apt-get install -y --no-install-recommends shellcheck \
    && rm -rf /var/lib/apt/lists/*

# A real browser for web/: scripts/ui_smoke_ci.py serves web/ statically
# and drives it headless (the same floor ci.yml's ui-smoke job enforces).
# Best-effort on purpose: the UI exerciser is optional to the audit, the
# Python build is not. CI asserts the smoke actually passes offline, so a
# broken browser install is caught before merge rather than hidden here.
RUN pip install playwright \
    && (playwright install --with-deps chromium \
        || echo "WARNING: Playwright/Chromium install failed; UI smoke unavailable in this image")

# Synthetic corpus so the API and UI have rows to serve with no bucket and
# no network: fictional cases, roles never individuals. Written to the same
# paths production uses (data/ is gitignored, never in the checkout).
ENV PROCESSED_ARTICLES_PATH=/src/data/processed/articles.jsonl \
    RAW_ARTICLES_PATH=/src/data/raw/articles.jsonl
RUN python .oss-scanner/seed_corpus.py

# Run the suite so the build log proves the image works. A failing test
# must not abort the build (scanner template convention): the log goes to
# primary_contact either way.
RUN (pytest -q -p no:cacheprovider || echo "WARNING: pytest reported failures") \
    && rm -rf /src/.pytest_cache

EXPOSE 8000
CMD ["bash"]
