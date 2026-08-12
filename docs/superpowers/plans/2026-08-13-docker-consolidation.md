# Docker Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Yuki's broken, duplicated Docker setup with one multi-stage Dockerfile, one entrypoint, one build script, and a portable dev compose file.

**Architecture:** Single `docker/Dockerfile` with `base` → `dev` / `prod` stages (prod last = default). All Python dependencies resolve from `pyproject.toml`; `celebichrono` comes from PyPI. Containers run as non-root user `yuki` (uid 1000); RabbitMQ stays in-container with state under `/tmp`. Dev compose bind-mounts the repo and optionally a CelebiChrono checkout via `CELEBI_DIR`.

**Tech Stack:** Docker (BuildKit), Docker Compose, bash, RabbitMQ, Python 3.10-slim.

**Spec:** `docs/superpowers/specs/2026-08-13-docker-consolidation-design.md`

## Global Constraints

- Build context is always the **repo root**; Dockerfile path is `docker/Dockerfile`.
- No hand-listed pip dependencies anywhere — deps come from `pyproject.toml` via `pip install .` / `pip install -e .`; the only extra pip installs are `build` and `celebichrono`.
- Runtime user is `yuki` (uid 1000); storage lives at `/home/yuki/.Yuki` (not `/root/.Yuki`).
- RabbitMQ state dirs must be under `/tmp/rabbitmq-data` (writable by any uid).
- `prod` must be the **final** stage in the Dockerfile so bare `docker build` and CI produce it.
- bash scripts use `set -euo pipefail` and resolve the repo root from their own path.
- Deviation from spec (spec-sanctioned fallback): the optional CelebiChrono mount uses a **committed empty placeholder directory** as the default compose source instead of `required: false`, because the placeholder works on every Compose version and needs no version detection.

---

### Task 1: Multi-stage Dockerfile and unified entrypoint

**Files:**
- Create: `docker/Dockerfile` (overwrite existing)
- Modify: `docker/entrypoint.sh` (overwrite existing)
- Delete: `docker/Dockerfile.dev`, `docker/entrypoint.dev.sh`, `docker/requirements.txt`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: image targets `dev` and `prod`; `/app/entrypoint.sh` inside both images; contract that `/app/CelebiChrono` may or may not exist at runtime (compose in Task 3 relies on this); `yuki` CLI on `PATH` at `/usr/local/bin/yuki`.

- [ ] **Step 1: Write the new `docker/Dockerfile`**

Replace the entire file with:

```dockerfile
# syntax=docker/dockerfile:1
# Multi-stage build for Yuki.
#   docker build --target dev  -t yuki:dev .
#   docker build --target prod -t yuki:<version> .   (prod is the default/final stage)

########## base ##########
FROM python:3.10-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        rabbitmq-server \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; storage dir created here so named volumes
# inherit yuki ownership on first mount.
RUN useradd --create-home --uid 1000 --shell /bin/sh yuki && \
    mkdir -p /home/yuki/.Yuki && \
    chown -R yuki:yuki /home/yuki

RUN pip install --no-cache-dir build

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 3315

########## dev ##########
# Editable install; docker-compose bind-mounts the repo over /app/Yuki
# (same path, so the editable install keeps working).
FROM base AS dev

WORKDIR /app/Yuki
COPY . /app/Yuki

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install celebichrono && \
    pip install -e .

USER yuki
WORKDIR /app
CMD ["/app/entrypoint.sh"]

########## prod (final stage = default) ##########
FROM base AS prod

WORKDIR /app/Yuki-src
COPY . /app/Yuki-src

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install celebichrono && \
    python -m build && \
    pip install dist/*.whl && \
    cd /app && rm -rf /app/Yuki-src

USER yuki
WORKDIR /app
CMD ["/app/entrypoint.sh"]
```

- [ ] **Step 2: Write the unified `docker/entrypoint.sh`**

Replace the entire file with:

```sh
#!/bin/sh
set -e

# RabbitMQ state lives in /tmp so the container can run as any uid.
export RABBITMQ_ALLOW_INPUT_NON_SENSITIVE_DATA=1
export RABBITMQ_MNESIA_BASE=/tmp/rabbitmq-data
export RABBITMQ_LOG_BASE=/tmp/rabbitmq-data
export RABBITMQ_PID_FILE=/tmp/rabbitmq-data/rabbit.pid

mkdir -p /tmp/rabbitmq-data

# Start RabbitMQ in background
rabbitmq-server &

# Wait until RabbitMQ is ready
python3 - <<'EOF'
import socket, time
while True:
    try:
        s = socket.create_connection(('127.0.0.1', 5672), timeout=1)
        s.close()
        break
    except OSError:
        print("Waiting for RabbitMQ...")
        time.sleep(2)
EOF

echo "RabbitMQ is up and running."

# Dev convenience: when a CelebiChrono checkout is mounted (compose
# CELEBI_DIR), prefer it and the mounted Yuki source over installed packages.
if [ -n "$(ls -A /app/CelebiChrono 2>/dev/null)" ]; then
    export PYTHONPATH="/app/CelebiChrono:/app/Yuki:${PYTHONPATH:-}"
fi

exec yuki server start
```

- [ ] **Step 3: Delete the dead files**

```bash
git rm docker/Dockerfile.dev docker/entrypoint.dev.sh docker/requirements.txt
```

- [ ] **Step 4: Verify both targets build**

Run:
```bash
docker build -f docker/Dockerfile --target dev -t yuki:dev-test .
docker build -f docker/Dockerfile --target prod -t yuki:prod-test .
```
Expected: both complete successfully. In the prod build log, confirm `paramiko` appears among installed packages (it comes from `pyproject.toml` — this was missing in the old prod image).

- [ ] **Step 5: Verify prod image is non-root and has no leftover source**

Run:
```bash
docker run --rm --entrypoint id yuki:prod-test
docker run --rm --entrypoint ls yuki:prod-test /app
```
Expected: `uid=1000(yuki) ...`; `/app` contains `entrypoint.sh` only (no `Yuki-src`).

- [ ] **Step 6: Clean up test tags and commit**

```bash
docker rmi yuki:dev-test yuki:prod-test
git add docker/Dockerfile docker/entrypoint.sh
git commit -m "refactor(docker): consolidate to multi-stage Dockerfile with non-root runtime"
```

---

### Task 2: build.sh script

**Files:**
- Create: `docker/scripts/build.sh`
- Delete: `docker/scripts/build-nightly.sh`

**Interfaces:**
- Consumes: `docker/Dockerfile` targets `dev`/`prod` from Task 1; version string from `pyproject.toml` (`version = "..."` line).
- Produces: CLI `docker/scripts/build.sh dev | prod [--tar] [--nightly]`; image tags `yuki:dev`, `yuki:<version>`, `yuki:latest`, `yuki-nightly:0.0.<date>-1`; tar files `yuki-<version>.tar` / `yuki-nightly-0.0.<date>-1.tar` in the repo root. CI (Task 4) keeps its own build step and does not call this script.

- [ ] **Step 1: Write `docker/scripts/build.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DOCKERFILE="docker/Dockerfile"

usage() {
    cat <<EOF
Usage: $(basename "$0") <mode> [options]

Modes:
  dev            Build the development image (yuki:dev)
  prod           Build the production image (yuki:<version>, yuki:latest)

Options (prod only):
  --tar          Export the image with docker save (yuki-<version>.tar)
  --nightly      Nightly naming: yuki-nightly:0.0.<date>-1, :nightly, :latest
EOF
}

mode="${1:-}"
if [ $# -gt 0 ]; then shift; fi

tar_export=false
nightly=false
for arg in "$@"; do
    case "$arg" in
        --tar) tar_export=true ;;
        --nightly) nightly=true ;;
        *) usage >&2; exit 1 ;;
    esac
done

cd "${REPO_ROOT}"

case "$mode" in
    dev)
        if $tar_export || $nightly; then
            echo "error: --tar/--nightly are only valid with prod" >&2
            exit 1
        fi
        docker build -f "${DOCKERFILE}" --target dev -t yuki:dev .
        echo "Built yuki:dev"
        ;;
    prod)
        if $nightly; then
            date_tag="$(date +%Y%m%d)"
            ref="yuki-nightly:0.0.${date_tag}-1"
            tags=(-t "${ref}" -t yuki-nightly:nightly -t yuki-nightly:latest)
            tar_name="yuki-nightly-0.0.${date_tag}-1.tar"
        else
            version="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml | head -n1)"
            if [ -z "$version" ]; then
                echo "error: could not read version from pyproject.toml" >&2
                exit 1
            fi
            ref="yuki:${version}"
            tags=(-t "${ref}" -t yuki:latest)
            tar_name="yuki-${version}.tar"
        fi
        docker build -f "${DOCKERFILE}" --target prod "${tags[@]}" .
        echo "Built ${ref}"
        if $tar_export; then
            docker save -o "${tar_name}" "${ref}"
            echo "Wrote ${tar_name}"
        fi
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
```

Then `chmod +x docker/scripts/build.sh`.

- [ ] **Step 2: Delete the old nightly script**

```bash
git rm docker/scripts/build-nightly.sh
```

- [ ] **Step 3: Verify usage errors**

Run:
```bash
docker/scripts/build.sh
docker/scripts/build.sh bogus
docker/scripts/build.sh dev --tar
```
Expected: all three print usage/error and exit non-zero (the third with `--tar/--nightly are only valid with prod`).

- [ ] **Step 4: Verify dev and prod builds and tags**

Run:
```bash
docker/scripts/build.sh dev
docker/scripts/build.sh prod
docker images | grep '^yuki'
```
Expected: `yuki:dev`, `yuki:1.0.0b2` (version read from pyproject.toml), and `yuki:latest` all exist.

- [ ] **Step 5: Verify tar export round-trips**

Run:
```bash
docker/scripts/build.sh prod --tar
docker rmi yuki:1.0.0b2
docker load -i yuki-1.0.0b2.tar
docker images | grep 'yuki.*1.0.0b2'
```
Expected: `yuki-1.0.0b2.tar` written; after `docker load`, the `yuki:1.0.0b2` image is present again. Delete the tar afterward (`rm yuki-1.0.0b2.tar`) — it is gitignored via `*.tar` in `.dockerignore`/`.gitignore` (confirm `git status` stays clean).

- [ ] **Step 6: Verify nightly naming (no full rebuild needed)**

Run:
```bash
docker/scripts/build.sh prod --nightly --tar
docker images | grep yuki-nightly
```
Expected: `yuki-nightly:0.0.<today>-1`, `:nightly`, `:latest` exist and `yuki-nightly-0.0.<today>-1.tar` is written (BuildKit cache makes this fast after Step 4). Clean up: `rm yuki-nightly-*.tar`.

- [ ] **Step 7: Commit**

```bash
git add docker/scripts/build.sh
git commit -m "feat(docker): add unified build.sh with version/nightly tagging and tar export"
```

---

### Task 3: Dev compose file

**Files:**
- Modify: `docker-compose.yml` (overwrite)
- Create: `docker/celebi-placeholder/.gitkeep`

**Interfaces:**
- Consumes: `dev` image target and `/app/CelebiChrono` mount contract from Task 1.
- Produces: `docker compose up` dev environment; `CELEBI_DIR` env var interface for the optional CelebiChrono mount; named volume `yuki-storage` at `/home/yuki/.Yuki`.

- [ ] **Step 1: Write the new `docker-compose.yml`**

Replace the entire file with:

```yaml
services:
  yuki:
    build:
      context: .
      dockerfile: docker/Dockerfile
      target: dev
    image: yuki:dev
    container_name: yuki-dev
    ports:
      - "3315:3315"
    volumes:
      - .:/app/Yuki
      - yuki-storage:/home/yuki/.Yuki
      # Optional CelebiChrono sibling checkout:
      #   CELEBI_DIR=../CelebiChrono docker compose up
      # Default is a committed empty placeholder, so no mount happens
      # and the PyPI celebichrono package is used.
      - type: bind
        source: ${CELEBI_DIR:-./docker/celebi-placeholder}
        target: /app/CelebiChrono
        bind:
          create_host_path: false
    environment:
      - FLASK_ENV=development
      - CELERY_BROKER_URL=amqp://localhost
    working_dir: /app
    stdin_open: true
    tty: true

volumes:
  yuki-storage:
```

- [ ] **Step 2: Create the placeholder directory**

```bash
mkdir -p docker/celebi-placeholder
touch docker/celebi-placeholder/.gitkeep
```

- [ ] **Step 3: Verify the stack comes up**

Run:
```bash
docker compose up -d --build
```
Wait for startup, then:
```bash
docker compose logs yuki | tail -5
curl -s -o /dev/null -w '%{http_code}' http://localhost:3315/
```
Expected: logs contain `RabbitMQ is up and running.`; curl prints a non-000 HTTP code (any response from Flask proves the server is up).

- [ ] **Step 4: Verify non-root and broker connectivity**

Run:
```bash
docker compose exec yuki id
docker compose exec yuki python -c "import socket; s=socket.create_connection(('127.0.0.1',5672),timeout=2); print('broker ok'); s.close()"
```
Expected: `uid=1000(yuki) ...` and `broker ok`.

- [ ] **Step 5: Verify hot reload mount**

Run:
```bash
docker compose exec yuki sh -c 'head -1 /app/Yuki/pyproject.toml'
head -1 pyproject.toml
```
Expected: identical output (bind mount live).

- [ ] **Step 6: Verify optional CelebiChrono mount**

Run (path on this machine; substitute any CelebiChrono checkout):
```bash
docker compose down
CELEBI_DIR=/Users/wave/workdir/Celebi/Celebi/CelebiChrono docker compose up -d
docker compose exec yuki python -c "import celebichrono; print(celebichrono.__file__)"
```
Expected: path under `/app/CelebiChrono/...` (mounted source wins). Then without `CELEBI_DIR`:
```bash
docker compose down
docker compose up -d
docker compose exec yuki python -c "import celebichrono; print(celebichrono.__file__)"
```
Expected: path under `/usr/local/lib/python3.10/site-packages/...` (PyPI package).

- [ ] **Step 7: Verify storage persistence**

Run:
```bash
docker compose exec yuki sh -c 'touch /home/yuki/.Yuki/persist-check'
docker compose restart
docker compose exec yuki ls /home/yuki/.Yuki/persist-check
```
Expected: file survives the restart. Clean up:
```bash
docker compose exec yuki rm /home/yuki/.Yuki/persist-check
docker compose down
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml docker/celebi-placeholder/.gitkeep
git commit -m "fix(docker): portable dev compose with optional CELEBI_DIR mount and non-root storage"
```

---

### Task 4: CI and docs

**Files:**
- Modify: `.github/workflows/docker-nightly.yml:44-53`
- Modify: `CLAUDE.md` (Docker Development section, Docker Setup section)

**Interfaces:**
- Consumes: `prod` default stage from Task 1; `build.sh` from Task 2; compose behavior from Task 3.
- Produces: nothing consumed by later tasks (final task).

- [ ] **Step 1: Add explicit target to the CI build step**

In `.github/workflows/docker-nightly.yml`, change the build step from:

```yaml
      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/Dockerfile
          push: true
```

to:

```yaml
      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/Dockerfile
          target: prod
          push: true
```

(Only the `target: prod` line is added; `tags:`, `labels:`, `cache-from:`, `cache-to:` lines stay as they are.)

- [ ] **Step 2: Update CLAUDE.md Docker Development commands**

Replace the `### Docker Development` section body with:

````markdown
```bash
# Start dev environment with hot-reload
# Source code is mounted as a volume; RabbitMQ starts inside the container
docker compose up

# Optionally develop against a local CelebiChrono checkout instead of PyPI
CELEBI_DIR=../CelebiChrono docker compose up

# Build images locally
docker/scripts/build.sh dev            # yuki:dev
docker/scripts/build.sh prod           # yuki:<version> + yuki:latest (version from pyproject.toml)
docker/scripts/build.sh prod --tar     # also exports yuki-<version>.tar via docker save
docker/scripts/build.sh prod --nightly # yuki-nightly:0.0.<date>-1 naming
```
````

- [ ] **Step 3: Update CLAUDE.md Docker Setup section**

Replace the `### Docker Setup (docker/)` bullet list with:

```markdown
- **Dockerfile**: Multi-stage — `base` (system deps, RabbitMQ, non-root `yuki` user) → `dev` (editable install) and `prod` (wheel install, default stage). Python deps resolve from `pyproject.toml`.
- **docker-compose.yml**: Dev environment (`docker compose up`); optional `CELEBI_DIR` env var mounts a local CelebiChrono checkout.
- **entrypoint.sh**: Starts in-container RabbitMQ (state in `/tmp`), waits for the broker, then `yuki server start`.
- **scripts/build.sh**: Local image builder — dev/prod targets, version or nightly tagging, optional tar export.
```

- [ ] **Step 4: Add a storage-path migration note to CLAUDE.md**

At the end of the Storage Structure section, add:

```markdown
Note: as of the Docker consolidation (2026-08), containers run as non-root user `yuki`, so in-container storage is `/home/yuki/.Yuki` (previously `/root/.Yuki`). Old `yuki-storage` volume contents under the root path are not migrated automatically.
```

- [ ] **Step 5: Verify**

Run:
```bash
git diff --stat
python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))" 2>/dev/null || ruby -e "require 'yaml'; YAML.load_file('docker-compose.yml')"
```
Expected: only the intended files changed; compose file parses as valid YAML. (The CI yaml change is one added line; eyeball `git diff .github/workflows/docker-nightly.yml`.)

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/docker-nightly.yml CLAUDE.md
git commit -m "docs(docker): update CLAUDE.md and pin CI to prod target"
```

---

### Task 5: Final end-to-end verification

**Files:**
- None (verification only).

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: go/no-go for the user to retire `~/workdir/Celebi/YukiDocker`.

- [ ] **Step 1: Clean slate rebuild**

```bash
docker compose down -v
docker/scripts/build.sh dev
docker/scripts/build.sh prod --tar
```

Expected: both succeed from the scripts alone.

- [ ] **Step 2: Full stack smoke test**

```bash
docker compose up -d
sleep 15
curl -s -o /dev/null -w '%{http_code}' http://localhost:3315/
docker compose exec yuki id
docker compose down
```

Expected: HTTP response from Flask; `uid=1000(yuki)`.

- [ ] **Step 3: Prod container smoke test**

```bash
docker run --rm -d --name yuki-prod-test -p 3316:3315 yuki:latest
sleep 15
curl -s -o /dev/null -w '%{http_code}' http://localhost:3316/
docker stop yuki-prod-test
```

Expected: HTTP response from Flask inside the prod image too.

- [ ] **Step 4: Confirm repo is clean and report**

```bash
git status
git log --oneline -5
```

Expected: clean tree, four new commits. Report to the user that `~/workdir/Celebi/YukiDocker` can now be retired (user deletes it themselves — per spec, not our action).
