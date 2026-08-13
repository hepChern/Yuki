# Runner Management Improvement — Design

Date: 2026-08-13

Scope: **two repos** — Yuki (server, this repo) and Celebi (client, `../Celebi`, package `CelebiChrono`). Sections are labeled per repo.

## Problem

Runner management spans Yuki (which owns the registry in `~/.Yuki/config.json`) and Celebi (a thin HTTP client). Today:

- **SSH runners cannot be registered from `celebi-cli`** — `register-runner` only takes `NAME URL SECRET BACKEND_TYPE`; the ssh fields exist only in Yuki's HTTP API, so users must hand-craft requests.
- **Native (local) runners have no settings at all** — workdir is hardcoded to `~/.Yuki/LocalWorkflows`, snakemake always runs with `--cores all` via PATH lookup. SSH runner settings (user, remote workdir, …) can only be set via raw API.
- **No capability/health testing** — there is no way to ask "does this runner have snakemake and conda available?" before submitting. `runner-connection` only checks TCP/SSH connectivity (and REANA ping).
- **Celebi client papercuts**: runner tab-completion cache is never written (always empty); port fallback is `localhost:5000` while the real default is `127.0.0.1:3315`; `add_host(host, url)` wrapper has an arity mismatch (latent crash); stale "UNUSED" annotation on `runners_url`.
- **Yuki server papercuts**: `register-runner` blindly appends duplicate names; `/machine-id/<unknown>` 500s; `/runners-url` KeyErrors on incomplete maps; `remove-runner` leaves stale `ssh_*` entries when the backend type was flipped away from ssh before removal.

## Decisions (from brainstorming)

- **Compat-first: additive changes only.** Existing endpoints keep their behavior and response shapes; new capabilities arrive as new endpoints or new optional parameters/keys. Old clients (chern-shell, scripts) are unaffected.
- **New per-runner data lives in a nested `runner_settings` map** keyed by runner id, instead of more top-level parallel maps. Old flat maps (`urls`, `tokens`, `backend_types`, `ssh_*`, …) stay untouched; ssh fields are read new-map-first with fallback to the old maps (no migration).
- **Capability testing runs on the Yuki server** (`GET /test-runner/<name>`, synchronous). Yuki holds the SSH credentials and local runners execute on the server host, so client-side testing is impossible/wrong. Sync execution with a client-side timeout (~30s) — no Celery task, no polling.
- **Test results persist** in a `runner_health` map and surface in `celebi-cli runners` output.
- Local runner settings in scope: **workdir, cores, mem_mb, conda_path, snakemake_path**. Rule-level (per-job) resources via `ContainerJob.snakemake_rule` are **not** in scope (YAGNI).
- Out of scope (explicit non-goals): token encryption/masking, REST verb cleanup (`remove-runner` stays GET), response-style unification, `job_status` runner-parameter dead code, `resubmit` stubs, interactive chern-shell prompt redesign.

## Yuki: data model

Two new top-level keys in `~/.Yuki/config.json`; all existing keys untouched:

```json
"runner_settings": {
  "<runner_id>": {
    "workdir": "/data/yuki-workflows",         // native; default ~/.Yuki/LocalWorkflows
    "cores": 8,                                 // native+ssh; default "all"
    "mem_mb": 16384,                            // native; optional
    "conda_path": "/opt/miniconda3/bin/conda",  // native+ssh; default PATH lookup
    "snakemake_path": "...",                    // native+ssh; default PATH lookup
    "ssh_host": "...", "ssh_user": "...", "ssh_key_path": "...",
    "ssh_port": 22, "remote_workdir": "..."     // ssh; new runners written here (and double-written to old maps)
  }
},
"runner_health": {
  "<runner_id>": {
    "status": "ok | failed | untested",
    "checked_at": "2026-08-13T10:00:00",
    "checks": {
      "connectivity":     {"ok": true},
      "snakemake":        {"ok": true, "version": "8.x", "path": "..."},
      "conda":            {"ok": false, "error": "not found in PATH"},
      "workdir_writable": {"ok": true}
    }
  }
}
```

Access goes through helpers in `Yuki/server/config.py`:

- `get_runner_settings(runner_id) -> dict` — returns the `runner_settings` entry (empty dict if absent).
- `get_ssh_config(runner_id) -> dict` — ssh fields, **runner_settings first, falling back** to the old `ssh_hosts`/`ssh_users`/`ssh_key_paths`/`ssh_ports`/`remote_workdirs` maps. Old ssh runners keep working with zero migration.
- `set_runner_health(runner_id, result)` / `get_runner_health(runner_id)`.

`remove-runner` cleanup widens: delete the `runner_settings` and `runner_health` entries, and purge old `ssh_*` maps **unconditionally** (fixes the stale-entry bug when backend type was flipped before removal).

## Yuki: API

### New endpoints (in `Yuki/server/routes/runner.py`)

| Endpoint | Method | Behavior |
|---|---|---|
| `/test-runner/<runner>` | GET | Synchronous capability probe, dispatched by backend type. **native**: on the Yuki host — locate snakemake (`snakemake_path` setting, else `shutil.which`), run `--version`; locate conda (`conda_path`, else PATH), run `--version`; check `workdir` exists and is writable (create if missing). **ssh**: paramiko-connect (existing pattern), then run the same probes **on the remote** (remote PATH or configured paths; check `remote_workdir` writable). **reana**: existing ping + token presence. **dry**: connectivity only, always ok. Result persisted to `runner_health`, returned as JSON `{"status", "checked_at", "checks": {...}}`. Unknown runner → 404 JSON. |
| `/runner-health/<runner>` | GET | Read-only: returns the persisted `runner_health` entry, or `{"status": "untested"}`. Never re-probes. |

Probes are best-effort: each check captures its own exception into `checks.<name> = {"ok": false, "error": ...}`; overall `status` is `failed` if any check fails. A per-probe timeout (e.g. 10s for SSH command execution) bounds the sync request.

### Extended endpoints (backward compatible)

- `POST /register-runner`: new optional form fields `workdir`, `cores`, `mem_mb`, `conda_path`, `snakemake_path` → stored in `runner_settings`. ssh fields (already supported) are now **double-written**: old `ssh_*` maps (so existing code paths keep working) *and* `runner_settings`. Response stays `"successful"`.
  - New validation: missing `url`/`token` → 400 with plain-text error (old clients raise on non-`"successful"` body already); duplicate runner name → 409 (fixes blind append).
- `PATCH /update-runner/<runner>`: accepts the five new fields, written to `runner_settings`.
- `GET /runners-config`: each runner dict gains `settings` (from `runner_settings`, `{}` if none) and `health` (from `runner_health`, `{"status": "untested"}` if none). Old clients ignore unknown keys. **Tokens still returned as before** (unchanged).
- `GET /remove-runner/<runner>`: verb and response unchanged; cleanup widened as above.

### Yuki bug fixes (behavioral bugs, not compat breaks)

- `/machine-id/<unknown>` → 404 instead of 500 KeyError.
- `/runners-url` tolerates runners missing from the `urls` map (skip) instead of KeyError.

## Yuki: consuming the settings

- **Native workflow** (`Yuki/kernel/native_workflow.py` + `run-workflow` in `Yuki/main.py`):
  - Execution dir: `runner_settings.workdir/<uuid>` when set, else `~/.Yuki/LocalWorkflows/<uuid>` (unchanged default).
  - `yuki run-workflow`: `--cores <cores>` (default `all`), `--resources mem_mb=<mem_mb>` when set, snakemake invoked via `snakemake_path` when set, `conda_path` used for conda activation/prefix when set.
- **SSH workflow** (`Yuki/kernel/ssh_workflow.py`):
  - `_load_ssh_config` switches to the merged `get_ssh_config` helper.
  - Generated `yuki_run.sh` applies `cores` (currently hardcoded `--cores all`); `conda_path`/`snakemake_path`, when set, are injected into remote commands.
- **REANA workflow**: untouched; consumes no new settings.
- Settings only affect **newly started** workflows. In-flight/completed workflows keep the paths recorded in their persisted state.

## Celebi: CLI (`../Celebi`)

### New command

- `celebi-cli test-runner <name>` → `GET /test-runner/<name>`; renders a result table (connectivity / snakemake version+path / conda version+path / workdir writable), failures in red with a remediation hint (e.g. "conda not found — set it via `update-runner --conda-path`"). Client timeout ~30s.

### Extended commands

- `celebi-cli runners`: table gains a `HEALTH` column (last test status + age, from the new `health` key in `/runners-config`) and shows backend/workdir summary from `settings`. Existing per-runner connection ping is kept.
- `celebi-cli register-runner`: new options `--ssh-host/--ssh-user/--ssh-key-path/--ssh-port/--remote-workdir` (ssh) and `--workdir/--cores/--mem-mb/--conda-path/--snakemake-path` (native). This closes the "cannot register ssh runners from the CLI" hole. `ChernCommunicator.register_runner` grows matching kwargs, sent as form fields.
- `celebi-cli update-runner`: same options added; `ChernCommunicator.update_runner` passes them through in the JSON settings body.

### Celebi bug fixes

1. Tab-completion cache: after successful `runners`/`register-runner`, write the runner name list into the readline cache key that `completions.py` reads (currently never written → completion always empty).
2. Port fallback `localhost:5000` → `127.0.0.1:3315` in `chern_communicator.py` and `reana_booking.py`.
3. Fix `add_host(host, url)` wrapper arity mismatch in `shell_modules/communication.py`.
4. Remove stale "UNUSED" annotation on `runners_url` in `chern_communicator.py`.

## Testing

### Yuki (`UnitTest/test_runner_routes.py` extended; existing mock style)

- `/test-runner`: mock `shutil.which`/`subprocess` (native probes), mock paramiko (ssh probes incl. failure cases), mock reana ping; assert ok/failed/untested paths and `runner_health` persistence.
- register/update with new fields: assert `runner_settings` contents and ssh double-write; duplicate-name 409; missing-field 400.
- `/runners-config` includes `settings`/`health`; `/remove-runner` widened cleanup; `/machine-id` 404; `/runners-url` tolerance.
- All `ConfigFile` access via temp dirs; never touch real `~/.Yuki`.

### Celebi (`UnitTest/test_cherncommunicator.py` extended)

- `test_runner` HTTP call + table rendering; register/update new kwargs form/JSON construction (mocked HTTP).
- Completion-cache write, port fallback, `add_host` fix.

### Manual end-to-end

`docker compose up` dev environment → register a local runner with `--workdir/--cores` → `test-runner` → submit a demo workflow → verify snakemake command line reflects the settings.

## Rollout / compat summary

- No config migration; old flat maps remain the source of truth for old fields; ssh reads fall back.
- No endpoint removed or changed in shape; new keys in `/runners-config` are additive.
- Old Celebi against new Yuki: works unchanged. New Celebi against old Yuki: `test-runner`/health column degrade gracefully (404 → "server too old" message; health shows `untested`).
