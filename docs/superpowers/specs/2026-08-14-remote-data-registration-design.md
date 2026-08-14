# Remote Data Registration & Data Command Renaming — Design

Date: 2026-08-14

Scope: **two repos** — Yuki (server, this repo) and Celebi (client, `../Celebi`, package `CelebiChrono`).

## Problem

Celebi's rawdata lifecycle has three sources: `import` (local file into a task), `send` (local data uploaded to DITE), and `use-data` (adopt an impression already hosted on DITE). Missing is the SSH-runner case: analysis data (LHCb files) often already lives on a compute farm. Today the only way to use it is to pull it to a laptop and `send` it back up — terabytes over a slow link. This design adds **register-data**: register data that lives on an SSH runner, compute its MD5 and copy it into a Yuki-managed staging area **on that runner** (zero network transfer through DITE), and create the matching impression so downstream tasks can reference it normally.

While adding this command, the data-command family gets renamed so every command's verb states its hosting direction (directional-verb scheme).

## Decisions (from brainstorming)

- **Naming — directional verbs**: `upload-data` (was `send`: local → DITE), `attach-data` (was `use-data`: DITE → local reference), `register-data` (new: ssh path → managed staging on that runner). `create-data`, `create-data-list`, `create-lhcb-ap-list`, `import` stay. Old names are **removed entirely** (no aliases). Kernel-internal function names (e.g. `VTask.send`) do not change — only the user-facing layer (CLI commands, shell functions, chern-shell `do_*`, tab completions).
- **register-data semantics**: copy (not reference-only) into the managed area `[remote-workdir]/impressions/<project_uuid>/<impression_uuid>/` — the `impressions/` directory reserved in the ssh-layout change. Managed by Yuki, survives source-path changes.
- **MD5 & impression identity** (mirrors `yuki-create-data` exactly):
  - `md5 = csys.dir_md5` semantics over the source directory — the **task** uuid.
  - A canonical rawdata task (`environment: rawdata`, `uuid: <md5>`, `descriptor`) is synthesized; `impression_uuid = VImpression().generate_imp_uuid(project_uuid, canonical_task, [])` — deterministic, so the local pointer task impress produces the same uuid.
  - The remote MD5 implementation (parallel `md5sum`) MUST be byte-identical in semantics to `csys.dir_md5` so the same data yields the same uuid regardless of registration path.
- **Execution model — Celery background job + polling**: hashing/copying can take hours; the HTTP request must not hold. `POST /register-remote-data` returns a job id immediately; the CLI polls `GET /register-remote-data/<job_id>` for stage-level progress (`hashing → copying → registering → done/failed`).
- **Fast copy on the runner**: mirror `yuki_create_data.fast_copy_tree` strategy remotely — `cp -a --reflink=auto` → hard-link → `rsync -a` → plain copy.
- **Idempotent registration**: `remote.json` records the source path; re-registering the same `(runner, path)` returns the existing impression without re-hashing/copying.
- **Runner binding**: the data is hosted on its registration runner. Submitting a workflow that needs it to a different runner **fails validation** with a clear error (future work: `collect` moves the data; no `--fetch` now).
- **No cleanup story** for the staging area in this iteration (deferred by user).
- **CLI dual mode** (mirrors `use-data`): inside a rawdata task → fill its `uuid`/`descriptor` from the response; not inside one → create a new pointer task; inside a non-rawdata task → error.

## Yuki: API

### `POST /register-remote-data`

Form/JSON: `runner` (name), `remote_path`, `project_uuid`, `descriptor` (optional; defaults to basename of `remote_path`).

Validation (400/404, JSON errors):
- runner unknown → 404
- runner backend not `ssh` → 400 ("register-data requires an ssh runner; native data should use upload-data")
- already registered for this `(runner, remote_path)` → returns the existing registration immediately (idempotent). If a registration job for the same `(runner, remote_path)` is still in flight, a duplicate POST returns that same `job_id` instead of starting a second job.

Success: returns `{"job_id": ...}` immediately. The job runs as a Celery task:

1. **hashing** — SSH `find <path> -type f -print0 | xargs -0 -P<N> md5sum | sort` (hash of the sorted list = `dir_md5`), matching `csys.dir_md5` semantics.
2. **copying** — fast-copy `remote_path` → `[remote-workdir]/impressions/<project_uuid>/<impression_uuid>/` (reflink → hardlink → rsync → copy fallback).
3. **registering** — synthesize the impression in `~/.Yuki/Storage/<project_uuid>/<impression_uuid>/` replicating `yuki-create-data` structure: `contents/` (canonical rawdata task), `config.json`, `status.json`, plus `remote.json`:
   ```json
   {"host_runner_id": "<runner_uuid>",
    "source_path": "<original remote path>",
    "remote_path": "[remote-workdir]/impressions/<project_uuid>/<impression_uuid>"}
   ```
   Status field aligned with what `yuki-create-data` writes (its `pending`/ready conventions), verified against `get_impression_info` consumers during implementation.

Job result: `{"uuid": "<md5>", "impression_uuid": ..., "descriptor": ...}`.

### `GET /register-remote-data/<job_id>`

`{"status": "hashing|copying|registering|done|failed", "progress": {...}, "result": {...}|null, "error": null|"..."}`. Progress during `copying` may be estimated (bytes copied via `du` polling) — stage-level progress is the contract; byte-precision is not required.

### New blueprint

`Yuki/server/routes/remote_data.py` (registered in `app.py` alongside the others) — data registration is not runner management; keep it separate from `runner.py`.

## Yuki: execution-side consumption

### SSH workflow staging (`Yuki/kernel/ssh_workflow.py`)

In `_upload_files_remote`, the input-job branch gains a case: if `~/.Yuki/Storage/<project>/<impression>/remote.json` exists:

- `host_runner_id == self.machine_id` → remote-local copy: SSH `cp -a --reflink=auto <remote_path>/. <remote_exec_path>/imp<short>/stageout/` (data never crosses the Yuki network).
- otherwise → error (defense-in-depth; the submit validation below should have blocked this).

### Submit validation (`Yuki/server/tasks.py`)

In `task_exec_impression`, after `VWorkflow.create` and before `workflow.run()`: walk `workflow.jobs`; for each input job whose impression has `remote.json` with `host_runner_id !=` the target `machine_uuid`, mark the workflow `failed` with:

```
Data impression <impression_uuid> is hosted on runner <host_name>.
Submit this workflow to <host_name>, or move the data via collect (coming later).
```

## Celebi: CLI & communicator

- `ChernCommunicator.register_remote_data(runner, remote_path, project_uuid, descriptor=None) -> job_id`, `register_remote_data_status(job_id) -> dict` (poll).
- New command `celebi-cli register-data <runner> <remote_path> [--descriptor X]` — starts the job, polls with a stage progress display, and on `done` performs the local pointer-task step: rawdata task context → fill `uuid`/`descriptor`; otherwise create the pointer task (same code path as `attach-data`); non-rawdata task context → error.
- Renames (old names deleted):
  - `use-data` → `attach-data`: CLI command, shell `use_data` → `attach_data`, chern_shell `do_use_data` → `do_attach_data`, completions.
  - `send` → `upload-data`: CLI command, shell `send` → `upload_data`, chern_shell `do_send` → `do_upload_data`.
  - Docs/celebi-skills examples updated.

## Testing

### Yuki (existing mock styles)

- `remote_data.py` route: validation errors (unknown runner, non-ssh backend, idempotent re-registration), job-id return, status endpoint states.
- Celery job: mocked SSH — hashing command shape (parallel md5sum), fast-copy fallback chain, impression synthesis matches `yuki-create-data` layout (contents/config/status/remote.json), `generate_imp_uuid` called with the canonical task.
- `ssh_workflow.py` staging: FakeSsh asserts remote `cp` target and reflink flag; host mismatch errors.
- `tasks.py` validation: input impression with mismatched `remote.json` → workflow failed with the exact message; matching host → runs.
- MD5-semantics test: remote hash helper produces the same value as `csys.dir_md5` on a fixture tree.

### Celebi

- `register-data` command: mock communicator job lifecycle (hashing → done), pointer-task fill/create/error paths.
- Renamed commands: `attach-data`, `upload-data` tests updated; old command names assert removed.

### Manual e2e

On pkufarm212: `register-data pkufarm212 <remote dir>` → poll progress → submit a demo workflow referencing it on pkufarm212 → verify remote staging copies locally on the runner; submit the same workflow to a different runner → verify validation error.

## Rollout / compat

- Server-side additive endpoint + one new staging branch + submit validation — old clients unaffected except renamed commands in Celebi (breaking for scripts using `use-data`/`send`; accepted per "完全更新").
- No config migration.
