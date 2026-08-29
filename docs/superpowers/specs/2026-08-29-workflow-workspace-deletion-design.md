# Design: `/delete-workflow` and homekeep deprecation

**Date:** 2026-08-29
**Status:** Approved for implementation
**Repos touched:** Yuki (server + kernel)

## Goal

Give Yuki an explicit, uniform operation to free a workflow's runner-side
workspace on all three backends (ssh, native, reana), and mark the old
`homekeep` command as outdated.

Background: ssh and native workflow workspaces are never cleaned up today —
`SshWorkflow` creates `<remote_workdir>/workflows/<project>/<uuid>` and nothing
ever removes it; native leaves `LocalWorkflows/<uuid>` forever;
`/homekeep/<project>` crashes with `AttributeError` on non-reana workflows
because only `ReanaWorkflow` implements `homekeep()`. That method conflates
two operations — collect results and free the workspace — which is why this
design splits them: collect stays as-is, deletion becomes its own operation.

## Operations model

- **New:** `GET /delete-workflow/<project_uuid>/<workflow_uuid>` frees the
  runner-side workspace of one workflow, on ssh, native, and reana runners.
- **`kill` unchanged** — stop-only, never deletes anything.
- **`homekeep` marked outdated** — `/homekeep/<project_uuid>` returns `410`
  with a pointer to `collect` + `/delete-workflow`.
- **The local mirror `~/.Yuki/Workflows/<project>/<workflow>` is always kept**
  — it is the only remaining record of the run (results.json, workflow.log,
  engine_logs.json).

## User-facing interface

### `GET /delete-workflow/<project_uuid>/<workflow_uuid>`

(The GET shape follows the house style of the other workflow routes, e.g.
`/kill`.)

| Response | Condition |
|---|---|
| `200` | `{"status": "deleted", "project_uuid": ..., "workflow": ..., "backend_type": ...}` |
| `404` | no workflow mirror at `~/.Yuki/Workflows/<project_uuid>/<workflow_uuid>` |
| `409` | workflow is running — `{"error": "workflow is running; kill it first"}` |
| `500` | backend failure (ssh unreachable, reana error, …) — `{"error": str(e)}` |

### `GET /homekeep/<project_uuid>`

Returns `410` with `{"error": "homekeep is outdated; collect results then free
the workspace with /delete-workflow/<project>/<workflow>"}`. The old
`ReanaWorkflow.homekeep()` method stays in place with an "outdated" docstring
(nothing calls it anymore).

## Safety model

- **No server-side data-safety gate.** The client decides whether the data is
  safe to lose, using `/whereabouts` (per-impression location registry) and
  `/runner-data/<runner>` (runner inventory) for its own confirmation.
- **One operation-safety guard:** deletion is refused with `409` while the
  workflow status is running (`translate_to_musical(status) ==
  IN_MOVEMENT`) — removing a live run's workspace corrupts execution. The
  status is read from the local results.json mirror (best available without
  hitting the runner). Other non-terminal states (e.g. a queued reana
  workflow) are deletable and act as an abort.

## Architecture

### Kernel

- `VWorkflow.delete_workspace()` — base raises `NotImplementedError`
  (placeholder, same pattern as `kill()`).
- `SshWorkflow.delete_workspace()` —
  `rm -rf <shlex.quote(remote_exec_path)>` through the existing
  `_SshConnection` (exec timeout 3600s, like purge). Logs the action.
- `NativeWorkflow.delete_workspace()` —
  `shutil.rmtree(self.local_exec_path, ignore_errors=True)`.
- `ReanaWorkflow.delete_workspace()` —
  `client.delete_workflow(self.get_name(), True, True, access_token)` — the
  same call the old homekeep used; raises `ImportError` when
  `REANA_AVAILABLE` is false (consistent with the other reana methods).

### Routes

- `routes/workflow.py`: resolve the mirror directory; `404` when missing.
  Load the workflow via `VWorkflow.create(project_uuid, [], workflow_uuid)`
  (the factory resolves the backend from the persisted `backend_type`).
  Guard running → `409`. Call `workflow.delete_workspace()` and return the
  success payload. Backend exceptions → `500`.
- `routes/status.py`: `/homekeep/<project_uuid>` returns the `410` notice
  instead of iterating workflows.

## Registry interplay

After a successful deletion, the next `update_distribution()` refresh drops
the deleted workflow's `workflow` entries (the runner listing is empty), and
`/runner-data/<runner>` shows the workspace gone — so the data-status
registry stays truthful without any special handling. The local mirror keeps
`/workflows/<project>` listing and engine-log history intact.

## Testing

- **Backend unit tests** (new `UnitTest/test_delete_workflow.py`):
  - ssh: mocked `_SshConnection` — the exec command is exactly
    `rm -rf '<remote_exec_path>'` (shlex-quoted);
  - native: tmp `local_exec_path` is removed, and a missing dir does not
    raise;
  - reana: mocked `client.delete_workflow` receives
    `(workflow_name, True, True, token)`;
  - base `VWorkflow.delete_workspace` raises `NotImplementedError`.
- **Route tests** (same file, using the `_app` pattern):
  - `200` happy path (mocked `VWorkflow.create`);
  - `404` without a mirror dir;
  - `409` when results.json status is running / in movement;
  - `500` when `delete_workspace` raises.
- **Homekeep test**: `/homekeep/<project>` returns `410` and never
  constructs workflows (protects against the old AttributeError crash).

## Files touched

| File | Change |
|---|---|
| `Yuki/kernel/vworkflow.py` | base `delete_workspace()` |
| `Yuki/kernel/ssh_workflow.py` | `delete_workspace()` |
| `Yuki/kernel/native_workflow.py` | `delete_workspace()` |
| `Yuki/kernel/reana_workflow.py` | `delete_workspace()`; homekeep docstring |
| `Yuki/server/routes/workflow.py` | `/delete-workflow` route |
| `Yuki/server/routes/status.py` | `/homekeep` → 410 |
| `UnitTest/test_delete_workflow.py` | new test file |
