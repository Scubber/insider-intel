# insider-intel — multi-stage build
#
#   base    shared foundation: pinned Python, non-root user, runtime deps
#   dev     toolchain + test deps + hot reload (used by docker-compose / devcontainer / CI)
#   corpus  helper stage so an optionally-present baked corpus doesn't break fresh clones
#   runtime slim Cloud Run image — FINAL stage, so `docker build .` (deploy_cloud_run.sh)
#           keeps producing the production image with no --target flag
#
# Pin by version (never latest). Bump deliberately.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user; uid 1000 matches the default WSL user so bind-mounted files keep sane ownership
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Dependency layer: only pyproject.toml invalidates it — source edits stay cached.
# Runtime deps are extracted from [project.dependencies] so we don't need the
# package source (or a stub) just to warm the cache.
COPY pyproject.toml ./
RUN python - <<'PY' > /tmp/requirements.txt && pip install -r /tmp/requirements.txt
import tomllib
with open("pyproject.toml", "rb") as f:
    project = tomllib.load(f)["project"]
print("\n".join(project["dependencies"]))
PY


# ---------------------------------------------------------------------------
FROM base AS dev

# git is needed by pre-commit; curl for compose/devcontainer healthcheck + debugging
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Dev/test dependency layer (also cached until pyproject.toml changes)
RUN python - <<'PY' > /tmp/requirements-dev.txt && pip install -r /tmp/requirements-dev.txt
import tomllib
with open("pyproject.toml", "rb") as f:
    project = tomllib.load(f)["project"]
print("\n".join(project["optional-dependencies"]["dev"]))
PY

COPY README.md ./
COPY apps ./apps
COPY shared ./shared
COPY tests ./tests
# .venv/.pytest_cache/.ruff_cache exist here so compose's anonymous volumes
# inherit app-user ownership instead of root
RUN pip install --no-deps -e . \
    && mkdir -p /app/data/raw /app/data/processed \
        /app/.venv /app/.pytest_cache /app/.ruff_cache /home/app/.cache \
    && chown -R app:app /app /home/app

USER app
# Bind-mounted repo is owned by the host user; tell git it's fine
RUN git config --global --add safe.directory /app

EXPOSE 8000
CMD ["uvicorn", "apps.search.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ---------------------------------------------------------------------------
# Baked corpus is optional: present on a deploy machine after ingest/process,
# absent in a fresh clone / CI. COPY from this stage tolerates the empty case.
FROM base AS corpus
COPY . /src
RUN mkdir -p /out \
    && (cp /src/data/processed/articles.jsonl /out/ 2>/dev/null \
        || echo "no baked corpus in build context (fine for dev/CI)")


# ---------------------------------------------------------------------------
# FINAL stage — what `docker build .` produces for Cloud Run
FROM base AS runtime

ENV PROCESSED_ARTICLES_PATH=/app/data/processed/articles.jsonl \
    RAW_ARTICLES_PATH=/app/data/raw/articles.jsonl \
    CORS_ORIGINS=https://intel.thederpweb.com,https://scubber.github.io,http://127.0.0.1:5500,http://localhost:5500 \
    PORT=8080

COPY README.md ./
COPY apps ./apps
COPY shared ./shared
RUN pip install --no-deps .

RUN mkdir -p /app/data/processed /app/data/raw && chown -R app:app /app/data
COPY --from=corpus /out/ /app/data/processed/

USER app
EXPOSE 8080

# Cloud Run sets PORT; default 8080 for local docker run
CMD ["sh", "-c", "uvicorn apps.search.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
