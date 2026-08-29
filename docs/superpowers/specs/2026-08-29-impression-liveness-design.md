# Design: impression & workflow liveness (`/live-set` sync)

**Date:** 2026-08-29
**Status:** Approved for implementation
**Repos touched:** Yuki (server + kernel), CelebiChrono (celebi_cli + kernel)

## Goal

Teach Yuki which impressions are **live** — the current version of a task or
algorithm, plus its transitive inputs — and which are **superseded**, so
that stale data on runners can be reclaimed safely with two commands:
`celebi-cli purge-stale-cache <runner>` (superseded impressions' cache
entries) and `celebi-cli purge-stale-workflows <runner>` (non-live
workflows' workspaces). Celebi owns the project graph and pushes the live
set to Yuki over a dedicated endpoint; Yuki stores it, derives workflow
liveness from it, and serves it.

Background: in Celebi, an impression is a *version* of a task or algorithm.
`impress()` writes the new impression uuid into the object's config.json
(`impression` pointer) and records the previous one in `impression_history`.
Today Yuki treats every impression equally: it cannot tell a current version
from a superseded one, so purge/GC decisions either ask the user per entry or
risk deleting the version the analysis actually uses.

## Semantics

- **Live impression**: the current version of a task or algorithm in the
  project (referenced by the object's `impression` pointer), plus the
  transitive input dependencies of every such version (walked through
  `dependencies` / `alias_to_impression`).
- **Superseded impression**: an impression present in an object's
  `impression_history` that no object currently points to.
- **Unknown**: an impression in neither list, or a project with no synced
  set. Unknown is **treated as live** — conservative: a stale sync can only
  miss GC opportunities, never destroy a current version.
- **Live workflow**: a workflow whose uuid appears as the `workflow` variable
  of a live impression's per-machine run config
  (`~/.Yuki/Storage/<project>/<impression>/<machine_id>/config.json`).

## User-facing interface

### Yuki

**`PUT /live-set/<project_uuid>`** — full-state sync (idempotent, atomic
replace). Body:

```json
{"live": ["<32-hex-uuid>", ...], "superseded": ["<32-hex-uuid>", ...]}
```

- Invalid entries (not 32-hex, or a uuid appearing in both lists) →
  `400` listing them; nothing is stored.
- On success Yuki derives `live_workflows` by scanning
  `Storage/<project_uuid>/` run configs for the live impressions, then
  atomically writes `.Yuki/Live/<project_uuid>.json`:

```json
{
  "live": [...], "superseded": [...],
  "live_workflows": [...], "updated": "<iso-utc>"
}
```

- Any failure during derivation or write → `500` with `{"error": str(e)}`
  and nothing stored (single atomic write; never a half-synced set).
- Response: `{"stored": true, "live": n, "superseded": n, "live_workflows": n}`.
- The file lives under `.Yuki/Live/`, deliberately **outside**
  `.Yuki/Bookkeep/` — `/bookkeeping` wipes Bookkeep on every project sync.

**`GET /live/<project_uuid>`** — `{"live_impressions": [...],
"live_workflows": [...], "superseded": [...], "updated": iso}`;
`404` `{"error": ...}` when no live.json exists for the project.

**`POST /purge-runner-cache`** — gains a `superseded=1` scope: when set
(project/impression filters must be absent), selects exactly the cache
entries whose impressions are explicitly superseded
(`liveness.impression_live(...) is False`) — the classification that makes
runner GC safe without per-entry confirmations. Dry-run lists them.
Existing explicit project/impression purges are unchanged.

**`POST /purge-runner-workflows`** — body `{"runner": <name>,
"dry_run": bool}`. Scans the local mirrors
`~/.Yuki/Workflows/<project>/<wf>` whose config `machine_id` equals the
runner's id (covers ssh, native, and reana uniformly); for each workflow
whose project's synced set explicitly excludes it
(`liveness.workflow_live(project, wf) is False`), and which is not running,
deletes the workspace via the per-backend `delete_workspace()` (#4
semantics; mirror always kept). Live workflows, running workflows, and
workflows without an explicitly-synced set (unknown) are skipped. Response:
`{"purged": [{"project", "workflow"}], "skipped": [{"project", "workflow",
"reason"}], "dry_run": bool}`; a per-workflow delete failure is a skip with
reason, never an abort. `404` unknown runner, `400` non-ssh/native/reana
backend.

### CelebiChrono (celebi-cli) — the two commands

- **`celebi-cli purge-stale-cache <runner> [--dry-run] [--yes]`** — calls
  `POST /purge-runner-cache` with `superseded=1`; confirmation prompt unless
  `--yes`; dry-run lists what would go. Prints the purged/skipped summary.
- **`celebi-cli purge-stale-workflows <runner> [--dry-run] [--yes]`** —
  calls `POST /purge-runner-workflows`; same prompt/dry-run pattern.
- **`celebi-cli sync-live`** — computes the project's live + superseded sets
  (walk all tasks/algorithms: `impression` pointer + `impression_history`;
  transitive inputs via `dependencies` / `alias_to_impression`), PUTs them to
  DITE, prints a summary. Best-effort: network failure prints a warning and
  exits 0 — a stale set is safe by the unknown-is-live rule. Required
  infrastructure for both purge commands.
- **`impress_command`** (`celebi_cli/commands/communication.py`) triggers a
  best-effort `sync-live` after impress succeeds. (There is no unimpress
  command; superseded arises from re-impress and object deletion — the
  full-set computation covers both.)

**Out of scope for this iteration** (kept as later work): `celebi-cli live`
view command; `live` fields on `/whereabouts` and `/runner-data`; the
`/delete-workflow` response live warning.

## Architecture

### Yuki

- **`Yuki/kernel/liveness.py`** (new) — single owner of the live registry:
  - `save_live_set(project_uuid, live, superseded)` — validate (32-hex),
    `derive_live_workflows(project_uuid, live)` (scan Storage run configs),
    atomic write (tmp + os.replace) of `.Yuki/Live/<project_uuid>.json`,
    return the summary dict.
  - `load_live_set(project_uuid)` → dict or None.
  - `impression_live(project_uuid, impression)` → True/False/None
    (None = no set; False = explicitly superseded; True otherwise).
  - `workflow_live(project_uuid, workflow_uuid)` → True/False/None from the
    stored `live_workflows`.
- **`Yuki/server/routes/liveness.py`** (new blueprint `liveness`, registered
  in `app.py`) — the `PUT /live-set/<project>` and `GET /live/<project>`
  routes.
- **`Yuki/kernel/workflow_purge.py`** (new) — `purge_stale_workflows(
  runner_id, dry_run)`: mirror scan (config `machine_id` == runner id) →
  keep only `workflow_live(...) is False` and non-running workflows →
  `VWorkflow.create(...).delete_workspace()` per workflow, collecting
  purged/skipped with reasons.
- **`remote_data_ops.purge_runner_cache`** gains a `superseded=False`
  parameter that filters the cache walk to explicitly-superseded
  impressions (using `liveness.impression_live(...) is False`); the
  `/purge-runner-cache` route passes `superseded` through.
- **`routes/workflow.py`** gains `POST /purge-runner-workflows` calling
  `workflow_purge.purge_stale_workflows`.

### CelebiChrono

- **`CelebiChrono/kernel/liveness.py`** (new) — `compute_live_sets(project)`
  returning `(live: list, superseded: list)`; pure project-graph logic, no
  network, usable by shell and cli.
- **`celebi_cli/commands/liveness.py`** (new) — `sync-live`,
  `purge-stale-cache`, and `purge-stale-workflows` commands, using the
  existing chern_communicator HTTP helpers and the existing purge CLI
  prompt/dry-run pattern.
- **`communication.py`** — impress hook calls `sync-live` best-effort.

## Error handling & safety rules

- Unknown is always live; only explicitly-superseded entries are ever
  auto-GC-able; a stale or missing set costs missed GC opportunities at
  worst.
- No background sweeper and no new scheduler (consistent with the
  explicit-ops-only model): "auto GC" means the user can purge with
  `superseded=1` and trust the classification, not that Yuki deletes in the
  background.
- The live registry never interacts with `distribution.json` refresh or
  `/bookkeeping`; they stay orthogonal.

## Testing

- **Yuki** (new `UnitTest/test_liveness.py` + updates to existing files):
  - `save_live_set` validation (bad uuid → error listing it; uuid in both
    lists → error), derivation from a tmp Storage with per-machine run
    configs, atomic write + load round-trip;
  - `impression_live` / `workflow_live` true/false/None semantics;
  - route tests: PUT happy path, PUT invalid uuids 400 (nothing stored),
    PUT failure 500 (nothing stored), GET 200, GET 404;
  - `purge_runner_cache` with `superseded=True` selects only superseded
    entries (dry-run lists them) and leaves live/unknown entries alone;
  - `purge_stale_workflows`: mirror scan filters by runner id; live,
    running, and unknown workflows are skipped with reasons; per-backend
    `delete_workspace` dispatched for the non-live ones; a delete failure
    becomes a skip; dry-run deletes nothing; route tests (200/404/400).
- **CelebiChrono**: `compute_live_sets` on a tmp project (task with current
  impression + history + inputs, algorithm, detached impression);
  `sync-live` against a fake DITE endpoint (existing mock-HTTP patterns);
  `purge-stale-cache` / `purge-stale-workflows` send the right payloads to
  a fake endpoint and honor `--dry-run`/`--yes`; impress hook fires the
  sync (mocked) and never raises.

## Files touched

| Repo | File | Change |
|---|---|---|
| Yuki | `Yuki/kernel/liveness.py` | new — registry owner |
| Yuki | `Yuki/kernel/workflow_purge.py` | new — stale-workflow purge |
| Yuki | `Yuki/server/routes/liveness.py` | new — blueprint (PUT/GET) |
| Yuki | `Yuki/server/app.py` | register blueprint |
| Yuki | `Yuki/kernel/remote_data_ops.py` | purge `superseded` scope |
| Yuki | `Yuki/server/routes/remote_data.py` | pass `superseded` through |
| Yuki | `Yuki/server/routes/workflow.py` | `POST /purge-runner-workflows` |
| Yuki | `UnitTest/test_liveness.py` + updates | tests |
| CelebiChrono | `kernel/liveness.py` | new — `compute_live_sets` |
| CelebiChrono | `celebi_cli/commands/liveness.py` | new — `sync-live`, `purge-stale-cache`, `purge-stale-workflows` |
| CelebiChrono | `celebi_cli/commands/communication.py` | impress hook |
