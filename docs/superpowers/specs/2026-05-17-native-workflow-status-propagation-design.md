# Dry workflow: per-job status propagation and failure surfacing

## Motivation

The dry-run/local workflow backend (`Yuki/kernel/native_workflow.py`) currently has
two visible problems that hurt observability:

1. **Per-job statuses are never updated.** After snakemake executes, individual
   `VJob.status.json` files stay in their pre-execution state (`PRELUDE`,
   `IN_MOVEMENT`). The workflow-level `results.json` knows whether the run
   succeeded, but the API/UI sees stale per-job state.

2. **Failures are not surfaced.** When a job fails, the existing
   `imp<short>/logs/celebi_user_step*.log` capture has the stderr, but nothing
   propagates it into a place the operator can see at a glance
   (e.g. `detailed_status`).

There is also a structural issue: `NativeWorkflow.update_workflow_status()`
(called by server polling) and `SnakemakeMonitor` (called by the
`yuki run-workflow` CLI) operate on the same on-disk artifacts but do not share
status logic. Two divergent implementations of "what is the workflow doing
right now" is the wrong number.

## Goals

- After a native workflow terminates, every non-input non-algorithm `VJob` has a
  status that reflects what snakemake actually did (`CODA` for success,
  `FAILED` for failure or skip-due-to-upstream-failure).
- For failed jobs, `detailed_status` carries a short tail of the user-command
  log so operators see the cause without opening files.
- Both the server polling path and the CLI execution path produce the same
  per-job state by going through a single propagation function.
- Independent failures stop cascading: when job A fails, parallel job B still
  gets a real outcome.

## Non-goals

- Making `NativeWorkflow.kill()` actually terminate the snakemake process and mark
  running jobs as `STOPPED`. (Tracked separately; out of scope here.)
- Changing the REANA backend.
- Refactoring `SnakemakeMonitor` ownership beyond what is necessary to call
  the new propagation function.
- Restructuring `file_staging.FileStager`.

## Design

### Where the truth lives

`.done` marker files in `~/.Yuki/LocalWorkflows/<workflow-uuid>/` are the
authoritative per-job signal. They already exist; the Snakefile generation
appends `touch {short_uuid}.done` as the final shell command of each rule
(`container_job.py`), so the marker is written if and only if every preceding
command exited zero.

A missing `.done` is ambiguous in two ways and we disambiguate them:

- **The job ran and failed.** Detectable by the presence of a non-empty
  `imp<short>/logs/` directory (the first rule command is `mkdir -p logs` and
  user commands redirect into `logs/celebi_user_step{i}.log 2>&1`).
- **The job never ran** (upstream failure cascaded). Detectable by an absent
  or empty `imp<short>/logs/` directory.

### The new method

```python
# Yuki/kernel/native_workflow.py

def propagate_job_statuses(self, workflow_terminal: bool = False) -> None:
    """Reconcile each VJob's status.json with the on-disk markers.

    Skips input jobs and algorithm jobs (they don't run in the workflow).
    Skips jobs already in a terminal status (per `is_terminal_status` in
    `status_constants.py`: CODA, FINAL_NOTE, FAILED, STOPPED, DELETED) so
    we do not churn settled state.

    When workflow_terminal is False, only promotes jobs to CODA (jobs whose
    .done now exists). Does NOT mark anything FAILED — the workflow may still
    be running.

    When workflow_terminal is True, additionally marks every remaining
    non-finished job as FAILED, with detailed_status sourced from the latest
    user log or a "skipped due to upstream failure" message.
    """
```

```python
def _read_job_log_tail(self, short_uuid: str, max_chars: int = 500) -> str:
    """Return the tail of the highest-indexed celebi_user_step*.log for a job.

    Returns "" when no logs/ directory exists or it contains no matching
    files. The highest index is chosen because execution proceeds
    sequentially through user commands; that file holds the most recent
    output, which is where the failure happened.
    """
```

Classification rules in `propagate_job_statuses`:

| `.done` exists | `workflow_terminal` | logs/ present | Result                                                              |
|----------------|---------------------|---------------|---------------------------------------------------------------------|
| yes            | (any)               | (any)         | `CODA`, clear detailed_status                                       |
| no             | False               | (any)         | leave unchanged                                                     |
| no             | True                | yes           | `FAILED`, detailed_status = log tail (up to 500 chars)              |
| no             | True                | no            | `FAILED`, detailed_status = "Skipped: upstream dependency failed before this job ran" |

Skip jobs whose current status satisfies `is_terminal_status` (`CODA`,
`FINAL_NOTE`, `FAILED`, `STOPPED`, `DELETED`).

### Callers

**Server polling path** — `NativeWorkflow.update_workflow_status()`:

After the existing block that writes `results` to `results.json`:

```python
workflow_terminal = status in ("finished", "failed")
self.propagate_job_statuses(workflow_terminal=workflow_terminal)
```

**CLI execution path** — `SnakemakeMonitor`:

Constructor gains `project_uuid` and `workflow_uuid`. `_finalize_results` and
`_handle_failure` call propagation as their last step:

```python
from .vworkflow import VWorkflow
workflow = VWorkflow.create(self.project_uuid, [],
                            uuid=self.workflow_uuid, mode="native")
workflow.propagate_job_statuses(workflow_terminal=True)
```

The CLI in `Yuki/main.py run_workflow` passes the two UUIDs when constructing
the monitor.

### `--keep-going`

`SnakemakeMonitor.execute_snakemake` adds the flag:

```python
cmd = [
    "snakemake",
    "--use-conda",
    "--conda-frontend", "conda",
    "--keep-going",
    "-j", str(cores),
]
```

This makes the "no `.done` = this specific job failed" signal accurate. Without
it, a single failure stops snakemake immediately and unrelated parallel jobs
get classified as failed even though they never ran.

### Edge cases

- `local_exec_path` missing entirely (workflow never executed): the marker
  iteration finds zero `.done` files; with `workflow_terminal=False` (the
  natural state in that case) nothing is changed.
- Multiple polls land at once: `VJob.set_status` writes a small JSON file
  (`status.json`); last-write-wins is acceptable since the inputs converge.
- Jobs already in a terminal status: left untouched — guards against
  churning settled state when a polling call happens after a job has been
  archived or already marked failed.

## Files touched

- `Yuki/kernel/native_workflow.py` — add `propagate_job_statuses`,
  `_read_job_log_tail`; hook into `update_workflow_status`.
- `Yuki/kernel/snakemake_monitor.py` — accept `project_uuid` and
  `workflow_uuid`; add `--keep-going`; call propagation in
  `_finalize_results` and `_handle_failure`.
- `Yuki/main.py` — pass `project_uuid` and `workflow_uuid` to
  `SnakemakeMonitor`.
- `UnitTest/test_native_workflow.py` — new file (see Tests).

## Tests

New file `UnitTest/test_native_workflow.py`. Each test uses `tmp_path` to build
a minimal Storage + LocalWorkflows layout and constructs `VJob` instances
directly without going through the full workflow boot sequence.

1. `test_propagate_done_jobs_become_coda` — both jobs have `.done` → both
   reach `CODA`.
2. `test_propagate_missing_done_terminal_becomes_failed` —
   `workflow_terminal=True`, missing `.done` with a populated
   `celebi_user_step0.log` → `FAILED`, `detailed_status` contains the log tail.
3. `test_propagate_missing_done_no_logs_becomes_failed_with_skip_message` —
   `workflow_terminal=True`, missing `.done` and absent `logs/` → `FAILED`
   with the upstream-dependency message.
4. `test_propagate_in_flight_leaves_jobs_unchanged` —
   `workflow_terminal=False`, missing `.done` → job status unchanged.
5. `test_propagate_skips_input_and_algorithm_jobs` — `is_input=True` and
   algorithm-type jobs are not modified, regardless of marker state.
6. `test_propagate_does_not_churn_terminal_status` — VJob already in `CODA`,
   `FINAL_NOTE`, or `FAILED` → left alone even if marker state would
   otherwise prescribe a change.
7. `test_read_job_log_tail_picks_highest_step_index` — three
   `celebi_user_step{0,1,2}.log` files → returns tail of step 2.
8. `test_read_job_log_tail_returns_empty_when_no_logs` — no logs dir → `""`.

**Manual smoke test (not automated)**:

Create a project with two parallel jobs, set one to `exit 1`, run
`yuki run-workflow <uuid>`, then verify:
- Failing job's `status.json` shows `FAILED` with stderr tail in
  `detailed_status`.
- Parallel succeeding job's `status.json` shows `CODA` (confirms
  `--keep-going` worked).

## Rollout

This change is internal to the dry backend and does not change the REANA
backend or any API shape. No migration is required: stale per-job statuses
on existing workflows will simply be reconciled the next time
`update_workflow_status` runs or `yuki run-workflow` re-executes.
