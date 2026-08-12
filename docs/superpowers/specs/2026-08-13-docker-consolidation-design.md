# Docker Setup Consolidation — Design

Date: 2026-08-13

## Problem

Yuki's Docker configuration has grown organically and is now broken and duplicated:

- **Four dependency lists** that have drifted: `pyproject.toml`, `docker/requirements.txt` (unused), and per-package `RUN pip install` lines in both Dockerfiles. `paramiko` (needed by the SSH runner) is missing from the prod image.
- **`docker/Dockerfile.dev` does not build**: `COPY Celebi /app/Celebi` references a directory that does not exist in the build context. It also installs CelebiChrono at `/app/Celebi` while the compose file and entrypoint expect `/app/CelebiChrono`.
- **`docker-compose.yml` hardcodes a personal path** (`/Users/wave/workdir/...`) for the CelebiChrono mount, breaking dev for anyone else.
- **Dead files**: `docker/entrypoint.dev.sh` is never used; `docker/requirements.txt` is commented out everywhere.
- **A second, older Docker setup** lives outside the repo at `~/workdir/Celebi/YukiDocker/`. Two things there are worth porting: the `docker save` tar-export workflow (`rootless/build.sh`) and the (never completed) goal of running rootless. After porting, YukiDocker is retired by the user.
- Everything runs as root; binaries land in `/root/.local/bin`.
- No version pinning; nightly images are not reproducible (out of scope to fully solve — see Non-goals).

## Decisions (from brainstorming)

- Full consolidation on the in-repo `docker/` directory; YukiDocker is retired after porting.
- Port from YukiDocker: **tar export workflow** and a **true non-root image**. Do not port: RabbitMQ management plugin, `install.sh`, PyPI-install Dockerfiles.
- RabbitMQ stays in-container (all-in-one deployment model preserved) for both dev and prod.
- CelebiChrono: PyPI (`celebichrono` package) in both images by default; dev optionally mounts a sibling checkout via the `CELEBI_DIR` env var.
- One multi-stage `docker/Dockerfile` with `base` → `dev` and `base` → prod targets; prod is the final stage so bare `docker build` and CI produce prod.
- One local build script, `docker/scripts/build.sh`, handling dev / prod / nightly / tar modes. `build-nightly.sh` is removed; its behavior lives behind `build.sh prod --nightly`.
- Prod images are tagged with the package **version** read from `pyproject.toml`, plus `:latest`; nightly keeps the `0.0.<date>-1` scheme.

## File layout after the change

```
docker/
  Dockerfile                # multi-stage: base → dev → prod (prod last = default)
  entrypoint.sh             # single unified entrypoint
  scripts/
    build.sh                # dev | prod [--tar] [--nightly]
docker-compose.yml          # dev only; target: dev; no personal paths
```

Deleted: `docker/Dockerfile.dev`, `docker/entrypoint.dev.sh`, `docker/requirements.txt`, `docker/scripts/build-nightly.sh`.

Updated: `.github/workflows/docker-nightly.yml` (add explicit `target: prod`), `CLAUDE.md` (Docker sections), `.dockerignore` if needed.

## Dockerfile design

### base stage
- `FROM python:3.10-slim`
- apt: `build-essential`, `rabbitmq-server`, `ca-certificates` (with `--no-install-recommends`, lists cleaned)
- Create non-root user `yuki` (fixed UID 1000) with home `/home/yuki`; create `/home/yuki/.Yuki` owned by `yuki` so the named volume inherits ownership on first mount.
- `pip install build` (needed by prod wheel build).
- `EXPOSE 3315`

### dev stage (FROM base)
- `COPY . /app/Yuki`
- `pip install celebichrono` (PyPI) then `pip install -e /app/Yuki` — dependencies resolve from `pyproject.toml`; no hand-maintained package list.
- BuildKit cache mount for pip (`RUN --mount=type=cache,target=/root/.cache/pip ...`) to keep rebuilds fast.
- Copies `docker/entrypoint.sh` to `/app/entrypoint.sh`.
- `USER yuki`, `CMD ["/app/entrypoint.sh"]`

### prod stage (FROM base) — final stage, the default
- `COPY . /app/Yuki-src`; build wheel (`python -m build`), `pip install dist/*.whl` and `celebichrono`; source tree removed afterward (only the installed package ships).
- Same entrypoint, `USER yuki`, `CMD ["/app/entrypoint.sh"]`.

Pip installs run as root into system site-packages during build; the `yuki` CLI lands in `/usr/local/bin`. Runtime is always `USER yuki`.

## Entrypoint (single script)

1. Export `RABBITMQ_MNESIA_BASE`, `RABBITMQ_LOG_BASE`, `RABBITMQ_PID_FILE` under `/tmp/rabbitmq-data` (writable by any UID) and `RABBITMQ_ALLOW_INPUT_NON_SENSITIVE_DATA=1`; `mkdir -p` the dir.
2. Start `rabbitmq-server` in the background (as the current user — works rootless because all RabbitMQ state is under `/tmp`).
3. Poll `127.0.0.1:5672` with the existing Python socket loop until the broker accepts connections.
4. If `/app/CelebiChrono` exists and is non-empty (optional dev mount), prepend it and `/app/Yuki` to `PYTHONPATH` so mounted source wins over installed packages. In prod neither mount exists and `PYTHONPATH` is untouched.
5. `exec yuki server start`.

## docker-compose.yml (dev)

- Builds `docker/Dockerfile` with `target: dev`; container name `yuki-dev`.
- Ports `3315:3315`; env `FLASK_ENV=development`, `CELERY_BROKER_URL=amqp://localhost`; `stdin_open`/`tty` kept.
- Volumes:
  - `.:/app/Yuki` (bind; hot reload — path matches the editable install location)
  - `yuki-storage:/home/yuki/.Yuki` (named volume; persists Storage)
  - Conditional CelebiChrono mount:
    ```yaml
    - type: bind
      source: ${CELEBI_DIR:-/nonexistent-celebi}
      target: /app/CelebiChrono
      bind:
        create_host_path: false
    ```
    guarded by compose's `required: false` (Compose ≥ 2.24), so it is skipped when `CELEBI_DIR` is unset and errors clearly if set to a missing directory. If `required: false` proves unavailable in the user's compose version, fall back to a committed empty placeholder directory as the default source.

## build.sh

`docker/scripts/build.sh` (bash, `set -euo pipefail`), run from anywhere (resolves repo root from its own path):

```
docker/scripts/build.sh dev             # docker build --target dev  -t yuki:dev
docker/scripts/build.sh prod            # docker build --target prod -t yuki:<version> -t yuki:latest
docker/scripts/build.sh prod --tar      # + docker save -o yuki-<version>.tar yuki:<version>
docker/scripts/build.sh prod --nightly  # tags yuki-nightly:0.0.<date>-1, :nightly, :latest;
                                        # with --tar also saves yuki-nightly-0.0.<date>-1.tar
```

- `<version>` is extracted from `pyproject.toml` (`version = "..."`) with a grep/sed one-liner — no second copy of the version anywhere.
- Unknown/missing arguments print usage and exit non-zero.

## CI

`.github/workflows/docker-nightly.yml` keeps its current triggers, tags, and registry; the build step gains `target: prod` explicitly (behavior is unchanged since prod is the final stage). No other CI changes.

## Non-goals

- Splitting RabbitMQ into a separate service/container.
- Dependency version pinning / lockfiles (noted as future work; nightly reproducibility is not solved by this change).
- Multi-arch builds (the old YukiDocker build used `--platform linux/amd64`; build.sh may accept an optional `--platform` passthrough if trivial, otherwise out of scope).
- Deleting `~/workdir/Celebi/YukiDocker` — the user does this after verification.

## Verification

1. `docker/scripts/build.sh dev` and `docker/scripts/build.sh prod` both succeed.
2. `docker compose up` → Flask answers on `localhost:3315`, Celery connects to the in-container broker; edits to the mounted source are visible in the container.
3. `CELEBI_DIR=<sibling checkout> docker compose up` → container imports CelebiChrono from the mount (check `celebichrono.__file__`).
4. Prod container runs as non-root (`docker run --rm yuki:<version> id` → uid 1000) and `yuki server status`-equivalent smoke check passes.
5. `build.sh prod --tar` produces a tar that `docker load` accepts.
6. Pylint/unit tests unaffected (no Python code changes).
7. Docs updated: CLAUDE.md Docker sections match the new reality.
