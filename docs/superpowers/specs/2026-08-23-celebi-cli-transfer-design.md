# Design: `celebi-cli transfer` command

**Date:** 2026-08-23  
**Status:** Approved for implementation  
**Repos touched:** CelebiChrono (CLI), Yuki (server + kernel)

## Goal

Add a `celebi-cli transfer <source> <destination>` command that transfers the
**results** (stageout files) of the current impression between:

- Yuki local storage (`yuki`)
- SSH runner managed impressions cache (`runner:<runner-id>`)
- another SSH runner managed impressions cache (`runner:<runner-id>`)

The command follows the same server-backed, progress-polling pattern already
used by `celebi-cli register-ssh-data`.

## User-facing interface

```bash
celebi-cli transfer <source> <destination> [--pattern <glob>] [--force]
```

- `<source>` / `<destination>`: one of
  - `yuki` — local Yuki storage for the current impression
  - `runner:<runner-id>` — managed impressions cache on that SSH runner
- `--pattern <glob>` — optional glob filter, e.g. `*.png`, applied to file
  paths relative to the stageout / cache root.
- `--force` — overwrite files that already exist at the destination.

The command uses the current Celebi project / impression context (identical to
`register-ssh-data`).

## What is transferred

Only **stageout files** (job results). The transfer operates on the impression's
managed cache / stageout directory:

- Yuki side: `~/.Yuki/Storage/<project_uuid>/<impression>/<machine_id>/stageout/`
  — one stageout dir per runner machine (see `impression_storage.py`); the
  transfer reads the union across machine dirs, and writes downloads under the
  source runner's machine dir.
- Runner side: `<remote_workdir>/impressions/<project_uuid>/<impression>/`

If the source/destination directory does not exist, it is created.

> **Amendment (2026-08-26):** the original spec assumed a flat
> `<impression>/stageout/` on the Yuki side; the real storage layout nests
> stageout under per-machine directories. `result_transfer._list_yuki_stageout`
> implements the union listing.

## Architecture

```
CelebiChrono                         Yuki server
─────────────────────────────────────────────────────────────
celebi_cli/commands/            ->   POST /transfer
file_operations.py                   (start Celery job)
     │                                    │
     │                              Celery worker
     │                              task_transfer_results
     │                                    │
     │<── GET /transfer/<job_id> ────────┘
     (poll progress)
```

## Yuki server changes

### New module: `Yuki/kernel/result_transfer.py`

Responsibilities:

1. Resolve source/destination paths from project/impression + location spec.
2. List matching files with sizes on both local filesystem and remote runners.
3. Execute the transfer:
   - `runner -> yuki`: SFTP download
   - `yuki -> runner`: SFTP upload
   - `runner -> runner`: stream through the Yuki host (source -> Yuki memory ->
     destination), updating progress per chunk.
4. Write job state/progress JSON and update it as the transfer proceeds.

Key function signature:

```python
def run_transfer(job_id: str, project_uuid: str, impression: str,
                 source: str, destination: str,
                 pattern: Optional[str], force: bool,
                 yuki_dir: str = "~/.Yuki") -> dict:
    """Run the transfer and return a report.

    source/destination strings are either "yuki" or "runner:<runner-id>".
    """
```

Progress format (written to `~/.Yuki/transfer-progress/<job_id>.json`):

```json
{
  "status": "running",
  "bytes_done": 12345,
  "bytes_total": 99999,
  "current_file": "plots/mass.png"
}
```

Final state:

```json
{
  "status": "done",
  "transferred": 10,
  "skipped": 2,
  "failed": 0,
  "bytes_total": 99999
}
```

### New Celery task: `Yuki/server/tasks.py`

```python
@celeryapp.task
def task_transfer_results(job_id, project_uuid, impression,
                          source, destination, pattern, force):
    from ..kernel import result_transfer
    result_transfer.run_transfer(...)
```

### New routes: `Yuki/server/routes/transfer.py`

Add to the existing `transfer` blueprint:

- `POST /transfer`
  - Body: `project_uuid`, `impression`, `source`, `destination`, `pattern`, `force`
  - Validate runner ids, generate `job_id`, start Celery task, return `{"job_id": ...}`
- `GET /transfer/<job_id>`
  - Read `~/.Yuki/transfer-progress/<job_id>.json`
  - Return `{"status": ..., "progress": {...}, "report": {...}}`

### Registration

`Yuki/server/app.py` already registers the `transfer` blueprint; only new
routes need to be added to `Yuki/server/routes/transfer.py`.

## CelebiChrono client changes

### `CelebiChrono/kernel/chern_communicator.py`

Add:

```python
def transfer(self, project_uuid, impression, source, destination,
             pattern=None, force=False):
    """POST /transfer and return the server response."""

def transfer_status(self, job_id):
    """GET /transfer/<job_id> and return the server response."""
```

### `CelebiChrono/interface/shell_modules/file_operations.py`

Add a `transfer(source, destination, pattern=None, force=False)` shell
function that:

1. Reads current project/impression context.
2. Calls `ChernCommunicator.transfer(...)`.
3. Polls `transfer_status(job_id)` every ~2 seconds.
4. Displays a `tqdm` byte-level progress bar using `bytes_done / bytes_total`.
5. Returns a `Message` with transferred/skipped/failed counts.

### `CelebiChrono/celebi_cli/commands/file_operations.py`

Add:

```python
@click.command(name="transfer")
@click.argument("source", type=str)
@click.argument("destination", type=str)
@click.option("--pattern", type=str, default=None)
@click.option("--force", is_flag=True, default=False)
def transfer_command(source, destination, pattern, force):
    from CelebiChrono.interface.shell import transfer
    ...
```

### `CelebiChrono/celebi_cli/cli.py`

Register `file_operations.transfer_command`.

## Error handling

- Unknown runner id -> 404 with clear message.
- Non-SSH runner -> 400 (only SSH runners have managed impressions cache).
- Source directory missing -> treat as empty, report 0 files transferred.
- SSH connection failure -> mark job failed, include exception message.
- Partial failures -> continue, report failed files in final state.

## Testing

### Yuki side

- `UnitTest/test_result_transfer.py`
  - Mock `_SshConnection` for upload/download/list.
  - Test all three directions with temp dirs.
  - Test `--pattern` filtering.
  - Test `--force` overwrite vs skip.
  - Test progress JSON writing.

### CelebiChrono side

- Mock `ChernCommunicator` responses for the CLI command.
- Test argument passing and progress bar behavior.

## Security / safety

- Reject path traversal in `--pattern` or file names.
- Only transfer files under the resolved source/destination roots.
- Keep the existing SFTP-only access model; no new open ports.

## Future extensions (out of scope)

- Transfer logs in addition to stageout (`--kind` option).
- Direct runner-to-runner copy when both runners can reach each other.
- Resume interrupted transfers.
