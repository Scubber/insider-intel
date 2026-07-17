# Development environment (Docker / WSL2)

Disposable containerized dev environment. Nothing installs on your host except
Docker itself; `make clean` leaves no residue.

## Quickstart

```bash
git clone https://github.com/Scubber/insider-intel.git
cd insider-intel
make up
```

That's it. No `.env` required — everything has safe local defaults.

| Service | URL / port | What |
|---------|------------|------|
| `app`   | http://localhost:8000 (docs at `/docs`) | FastAPI search/reader API, hot reload |
| `ui`    | http://localhost:5500 | Static UI from `web/` (live via bind mount) |
| `db`    | localhost:5432 | Postgres 16 sidecar (data plane placeholder) |

To customize, `cp .env.example .env` and edit — compose picks it up on next `make up`.

## The workflow loop

```bash
make up        # start (or restart after config changes)
# edit code on the WSL filesystem — uvicorn reloads the API automatically
make test      # pytest inside the container
make lint      # ruff check
make fmt       # ruff format + autofix
make precommit # all hooks incl. secrets scan (no host toolchain needed)
make logs      # follow output
make shell     # bash inside the app container
make db-shell  # psql into the sidecar
make down      # stop, keep data
```

CI (`.github/workflows/ci.yml`) runs `make build`, `make lint`, `make test` —
the identical commands — so green locally means green in CI.

Populate a corpus inside the container (writes to `data/` on your checkout,
which is gitignored):

```bash
make shell
python -m apps.aggregator ingest --feeds-file apps/aggregator/feeds.example.json -v
python -m apps.aggregator process --force
```

### VS Code

"Dev Containers: Reopen in Container" from the WSL window attaches VS Code into
the running compose `app` service (`.devcontainer/devcontainer.json` reuses
`docker-compose.yml`; it doesn't define a second image). Python, Pylance, and
Ruff extensions install inside the container.

## Resetting to a clean environment

```bash
make db-reset   # nuke ONLY the Postgres volume, restart a fresh DB
                # (one-liner equivalent: docker volume rm -f insider-intel_pgdata)
make rebuild    # rebuild images with --no-cache and restart
make clean      # FULL teardown: containers, all volumes, local images, dangling layers
rm -rf data/    # optional: also drop the JSONL corpus on your checkout
```

`make clean && make up` reproduces the environment from scratch; that exact
sequence is exercised in CI-equivalent verification.

## Data plane

The app currently stores articles as JSONL under `data/`. The Postgres sidecar
is the *default future* data plane, not a hard dependency. The only DB wiring
is the `DATABASE_URL` env var, consumed exclusively through
[`shared/settings.py`](../shared/settings.py) (`get_settings().database_url`).
Keep it that way: when the data plane is decided (or swapped to something
else), only the compose `db` service and that one setting change — no
connection strings anywhere in application code.

## Production image

`docker build .` still produces the Cloud Run image (the `runtime` stage is
final, so `scripts/deploy_cloud_run.sh` works unchanged). The baked corpus
(`data/processed/articles.jsonl`) is now optional at build time: present on a
deploy machine, absent in fresh clones/CI. Dev-only tooling lives in the `dev`
stage and never ships.

## Browser automation (Playwright MCP)

[`.mcp.json`](../.mcp.json) registers a project-scoped [Playwright MCP](https://github.com/microsoft/playwright-mcp)
server so Claude Code can drive and screenshot the local UI (e.g. verify a web/
change on :5500 or :5510 visually). It runs the official
`mcr.microsoft.com/playwright/mcp` Docker image over stdio — **Docker is the
only requirement, no Node.js on the host**. `--network=host` lets the
containerized browser reach the compose stacks on `localhost`.

The server loads on your next Claude Code session in this repo; project-scoped
MCP servers prompt once for approval before first use. Nothing runs until a
session actually invokes it (`docker run --rm` per invocation, no residue).

Why not the Claude in Chrome/Edge extension: it is explicitly not supported
when Claude Code runs inside WSL (per the official Claude Code docs), so the
containerized Playwright route is the reliable option here.

## WSL-specific troubleshooting

**Keep the repo on the WSL ext4 filesystem** (e.g. `~/insider-intel`), *not*
`/mnt/c/...`. The 9p bridge to the Windows drive makes file watching unreliable
and slow — uvicorn's reloader and pytest both suffer. If `git rev-parse
--show-toplevel` starts with `/mnt/`, move the clone into the Linux home.

**File watching doesn't trigger reloads** → almost always the repo is on
`/mnt/c` (see above). The bind mount itself (`.:/app`) is inotify-friendly on
ext4.

**CRLF line endings** — `.gitattributes` normalizes everything to LF and the
`mixed-line-ending` pre-commit hook enforces it. If Git on Windows touched the
tree, also set `git config core.autocrlf false` in this repo. Symptoms of CRLF
damage: `bash\r: command not found` in containers, phantom whole-file diffs.

**Docker not found in WSL** — either enable Docker Desktop → Settings →
Resources → WSL Integration for this distro, or install native Docker Engine
inside WSL (`apt install docker.io docker-compose-v2 docker-buildx`, add
yourself to the `docker` group, `newgrp docker` or re-login). This setup was
built and verified against native Docker Engine in WSL.

**`uvicorn: not found` inside a freshly built image / "legacy builder"
deprecation warning** — the Dockerfile uses BuildKit heredocs, which the legacy
builder silently mangles. BuildKit is the default everywhere modern (Docker
Desktop, GitHub runners), but Ubuntu's bare `docker.io` package needs
`apt install docker-buildx`. `docker compose build` always uses BuildKit.

**Reaching the app from Windows** — WSL2 forwards `localhost` automatically:
http://localhost:8000 and http://localhost:5500 work in a Windows browser. If
they don't: check nothing on Windows already binds those ports
(`netstat -ano | findstr :8000` in PowerShell), and if you've set
`networkingMode=mirrored` in `.wslconfig`, ensure Windows Firewall allows the
ports. Reaching the app from *other machines* on your LAN requires a Windows
`netsh interface portproxy` rule — out of scope here.

**Ports already allocated on `make up`** — a previous Postgres or dev server on
the host is bound to 5432/8000/5500. Override in `.env` (`POSTGRES_PORT`,
`SEARCH_PORT`, `UI_PORT`) and `make up` again.
