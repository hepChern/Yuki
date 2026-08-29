# Workflow Workspace Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /delete-workflow/<project_uuid>/<workflow_uuid>` to free a workflow's runner-side workspace on ssh/native/reana backends, and deprecate `/homekeep` (410).

**Architecture:** One `delete_workspace()` method per workflow backend (ssh `rm -rf` via SFTP exec, native `shutil.rmtree`, reana `client.delete_workflow`), a placeholder raising `NotImplementedError` on the `VWorkflow` base, one route in `routes/workflow.py` (404 without a mirror, 409 while running, 500 on backend failure), and a `410` stub for `/homekeep` in `routes/status.py`. The local `~/.Yuki/Workflows/<project>/<workflow>` mirror is always kept.

**Tech Stack:** Python, pytest + unittest.mock, Flask blueprints, Paramiko (`_SshConnection`), reana-client.

**Spec:** `docs/superpowers/specs/2026-08-29-workflow-workspace-deletion-design.md`

## Global Constraints

- No new dependencies; no changes to `pyproject.toml`.
- Route style follows the existing house style: GET routes return plain JSON via `jsonify`; errors are `{"error": str(e)}` with status 400/404/409/500 as in the spec.
- Pylint must stay clean on changed files when run as:
  `pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" <files>`
- `kill()` behavior must not change (stop-only).
- The local mirror `~/.Yuki/Workflows/<project>/<workflow>` is never deleted.
- Test command: `python -m pytest UnitTest/<file> -q` (full suite: `python -m pytest UnitTest/ -q`).
- Existing tests (384 passing) must stay green at every commit.

---

### Task 1: Base `VWorkflow.delete_workspace()` placeholder

**Files:**
- Modify: `Yuki/kernel/vworkflow.py` (append after the existing `kill()` method at the end of the class)
- Test: `UnitTest/test_delete_workflow.py` (new)

**Interfaces:**
- Produces: `VWorkflow.delete_workspace()` — no parameters, raises `NotImplementedError` in the base. Subclasses in Tasks 2–4 override it.

- [ ] **Step 1: Write the failing test**

Create `UnitTest/test_delete_workflow.py`:

```python
"""Tests for workflow workspace deletion (delete_workspace + routes)."""
# pylint: disable=protected-access
import os
import json
from unittest import mock

import pytest


def test_vworkflow_delete_workspace_not_implemented():
    """The base workflow has no generic way to delete a workspace."""
    from Yuki.kernel.vworkflow import VWorkflow
    workflow = VWorkflow.__new__(VWorkflow)
    with pytest.raises(NotImplementedError):
        workflow.delete_workspace()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: FAIL — `AttributeError: 'VWorkflow' object has no attribute 'delete_workspace'`

- [ ] **Step 3: Write minimal implementation**

In `Yuki/kernel/vworkflow.py`, after the existing `kill()` method (the last method of the class):

```python
    def delete_workspace(self):
        """Delete the runner-side workspace - must be implemented by subclass.

        Raises:
        - NotImplementedError if subclass does not implement workspace deletion.
        """
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/vworkflow.py UnitTest/test_delete_workflow.py
git commit -m "feat(kernel): base delete_workspace placeholder on VWorkflow"
```

---

### Task 2: `SshWorkflow.delete_workspace()`

**Files:**
- Modify: `Yuki/kernel/ssh_workflow.py` (add after the existing `kill()` method; `import shlex` already exists at the top of the file)
- Test: `UnitTest/test_delete_workflow.py`

**Interfaces:**
- Consumes: `self._ssh()` context manager (existing), `self.remote_exec_path` (existing attribute), `self.logger(msg)` (existing).
- Produces: `SshWorkflow.delete_workspace()` — executes `rm -rf <shlex.quote(remote_exec_path)>` with `timeout=3600`; raises `RuntimeError` when the remote exit code is nonzero.

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_delete_workflow.py`:

```python
def test_ssh_delete_workspace_removes_remote_dir():
    """The remote workspace is deleted with a quoted rm -rf command."""
    from Yuki.kernel.ssh_workflow import SshWorkflow
    workflow = SshWorkflow.__new__(SshWorkflow)
    workflow.remote_exec_path = "/remote/workflows/proj/wf one"
    workflow.logger = lambda msg: None

    ssh = mock.MagicMock()
    ssh.__enter__.return_value = ssh
    ssh.__exit__.return_value = False
    ssh.exec.return_value = ("", "", 0)
    workflow._ssh = mock.MagicMock(return_value=ssh)

    workflow.delete_workspace()

    ssh.exec.assert_called_once()
    cmd = ssh.exec.call_args[0][0]
    assert cmd == "rm -rf '/remote/workflows/proj/wf one'"
    assert ssh.exec.call_args[1]["timeout"] == 3600


def test_ssh_delete_workspace_failure_raises():
    """A nonzero remote exit code surfaces as a RuntimeError."""
    from Yuki.kernel.ssh_workflow import SshWorkflow
    workflow = SshWorkflow.__new__(SshWorkflow)
    workflow.remote_exec_path = "/remote/workflows/proj/wf1"
    workflow.logger = lambda msg: None

    ssh = mock.MagicMock()
    ssh.__enter__.return_value = ssh
    ssh.__exit__.return_value = False
    ssh.exec.return_value = ("", "no such file", 1)
    workflow._ssh = mock.MagicMock(return_value=ssh)

    with pytest.raises(RuntimeError):
        workflow.delete_workspace()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 2 FAIL — `AttributeError: 'SshWorkflow' object has no attribute 'delete_workspace'`

- [ ] **Step 3: Write minimal implementation**

In `Yuki/kernel/ssh_workflow.py`, after `kill()`:

```python
    def delete_workspace(self):
        """Delete the remote workflow workspace on the runner."""
        self.logger(f"[SSH] Deleting remote workspace: {self.remote_exec_path}")
        with self._ssh() as ssh:
            out, err, code = ssh.exec(
                f"rm -rf {shlex.quote(self.remote_exec_path)}",
                timeout=3600)
            if code != 0:
                raise RuntimeError(
                    f"Failed to delete remote workspace: "
                    f"{err or out} (exit {code})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/ssh_workflow.py UnitTest/test_delete_workflow.py
git commit -m "feat(kernel): ssh delete_workspace removes the remote workspace"
```

---

### Task 3: `NativeWorkflow.delete_workspace()`

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (add after the existing `kill()` method; `import shutil` already exists at the top of the file)
- Test: `UnitTest/test_delete_workflow.py`

**Interfaces:**
- Consumes: `self.local_exec_path` (existing attribute), `self.logger(msg)`.
- Produces: `NativeWorkflow.delete_workspace()` — removes `local_exec_path`; a missing directory must not raise.

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_delete_workflow.py`:

```python
def test_native_delete_workspace_removes_local_dir(tmp_path):
    """The local execution workspace is removed."""
    from Yuki.kernel.native_workflow import NativeWorkflow
    workflow = NativeWorkflow.__new__(NativeWorkflow)
    workflow.local_exec_path = str(tmp_path / "wf1")
    workflow.logger = lambda msg: None
    os.makedirs(workflow.local_exec_path)
    with open(os.path.join(workflow.local_exec_path, "a.done"), "w",
              encoding="utf-8") as f:
        f.write("x")

    workflow.delete_workspace()

    assert not os.path.exists(workflow.local_exec_path)


def test_native_delete_workspace_missing_dir_no_raise(tmp_path):
    """Deleting an already-gone workspace does not raise."""
    from Yuki.kernel.native_workflow import NativeWorkflow
    workflow = NativeWorkflow.__new__(NativeWorkflow)
    workflow.local_exec_path = str(tmp_path / "gone")
    workflow.logger = lambda msg: None

    workflow.delete_workspace()  # no raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 2 FAIL — `AttributeError: 'NativeWorkflow' object has no attribute 'delete_workspace'`

- [ ] **Step 3: Write minimal implementation**

In `Yuki/kernel/native_workflow.py`, after `kill()`:

```python
    def delete_workspace(self):
        """Delete the local execution workspace."""
        self.logger(f"[LOCAL] Deleting local workspace: {self.local_exec_path}")
        shutil.rmtree(self.local_exec_path, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_delete_workflow.py
git commit -m "feat(kernel): native delete_workspace removes the local workspace"
```

---

### Task 4: `ReanaWorkflow.delete_workspace()` + homekeep docstring

**Files:**
- Modify: `Yuki/kernel/reana_workflow.py` (add after the existing `homekeep()` method; `REANA_AVAILABLE` / `client` already exist at module level)
- Test: `UnitTest/test_delete_workflow.py`

**Interfaces:**
- Consumes: `REANA_AVAILABLE`, `client`, `self.set_environment(machine_id)`, `self.get_name()`, `self.get_access_token(machine_id)`, `self.machine_id`.
- Produces: `ReanaWorkflow.delete_workspace()` — calls `client.delete_workflow(name, True, True, token)`; raises `ImportError` when `REANA_AVAILABLE` is false.

- [ ] **Step 1: Write the failing test**

Append to `UnitTest/test_delete_workflow.py`:

```python
def test_reana_delete_workspace_calls_client():
    """The online workflow is deleted with workspace + all-runs flags."""
    from Yuki.kernel import reana_workflow
    workflow = reana_workflow.ReanaWorkflow.__new__(
        reana_workflow.ReanaWorkflow)
    workflow.machine_id = "r1"
    workflow.get_name = mock.MagicMock(return_value="w-proj-wf1")
    workflow.get_access_token = mock.MagicMock(return_value="tok")
    workflow.set_environment = mock.MagicMock()

    with mock.patch.object(reana_workflow, "REANA_AVAILABLE", True), \
            mock.patch.object(reana_workflow, "client") as client:
        workflow.delete_workspace()

    client.delete_workflow.assert_called_once_with(
        "w-proj-wf1", True, True, "tok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 1 FAIL — `AttributeError: 'ReanaWorkflow' object has no attribute 'delete_workspace'`

- [ ] **Step 3: Write minimal implementation**

In `Yuki/kernel/reana_workflow.py`, after `homekeep()`:

```python
    def delete_workspace(self):
        """Delete the online workflow on the REANA server."""
        if not REANA_AVAILABLE:
            raise ImportError("reana_client is not available")
        self.set_environment(self.machine_id)
        client.delete_workflow(
            self.get_name(),
            True, True,
            self.get_access_token(self.machine_id)
        )
```

- [ ] **Step 4: Mark homekeep outdated in its docstring**

In `Yuki/kernel/reana_workflow.py`, change the `homekeep()` docstring's first line from
`"""Perform homekeeping tasks for the workflow.` to:

```python
    def homekeep(self):
        """Outdated: use collect plus delete_workspace (GET /delete-workflow).
        Perform homekeeping tasks for the workflow.
        Download all the results for the jobs in the workflow.
        """
```

(Keep the rest of the method body unchanged — nothing calls it anymore.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add Yuki/kernel/reana_workflow.py UnitTest/test_delete_workflow.py
git commit -m "feat(kernel): reana delete_workspace; mark homekeep outdated"
```

---

### Task 5: `GET /delete-workflow/<project_uuid>/<workflow_uuid>` route

**Files:**
- Modify: `Yuki/server/routes/workflow.py` (imports at the top + new route at the end)
- Test: `UnitTest/test_delete_workflow.py`

**Interfaces:**
- Consumes (from earlier tasks): `VWorkflow.create(project_uuid, [], workflow_uuid)` (existing factory), `workflow.status()` (existing), `workflow.delete_workspace()` (Tasks 1–4), `workflow.backend_type()` (existing).
- Produces: HTTP route `GET /delete-workflow/<project_uuid>/<workflow_uuid>` with responses exactly as in the spec table.

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_delete_workflow.py`:

```python
def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _mirror(tmp_path, project, workflow, status):
    """Create the workflow mirror dir with a results.json status."""
    wf_dir = tmp_path / ".Yuki" / "Workflows" / project / workflow
    wf_dir.mkdir(parents=True)
    with open(wf_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump({"results": {"status": status}}, f)


def _mock_workflow(status="finished"):
    workflow = mock.MagicMock()
    workflow.status.return_value = status
    workflow.backend_type.return_value = "ssh"
    return workflow


def test_delete_workflow_deletes_and_reports(monkeypatch, tmp_path):
    """A terminal workflow's workspace is deleted with a success payload."""
    from Yuki.server.routes import workflow as workflow_routes
    monkeypatch.setenv("HOME", str(tmp_path))
    _mirror(tmp_path, "proj", "wf1", "finished")
    wf = _mock_workflow("finished")

    with mock.patch.object(workflow_routes, "VWorkflow") as vwf:
        vwf.create.return_value = wf
        r = _app(workflow_routes.bp).test_client().get(
            "/delete-workflow/proj/wf1")

    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "deleted"
    assert body["project_uuid"] == "proj"
    assert body["workflow"] == "wf1"
    assert body["backend_type"] == "ssh"
    wf.delete_workspace.assert_called_once_with()


def test_delete_workflow_unknown_workflow_404(monkeypatch, tmp_path):
    """A workflow without a mirror directory gets a 404."""
    from Yuki.server.routes import workflow as workflow_routes
    monkeypatch.setenv("HOME", str(tmp_path))
    r = _app(workflow_routes.bp).test_client().get(
        "/delete-workflow/proj/nope")
    assert r.status_code == 404


def test_delete_workflow_running_409(monkeypatch, tmp_path):
    """A running workflow is refused with a 409."""
    from Yuki.server.routes import workflow as workflow_routes
    monkeypatch.setenv("HOME", str(tmp_path))
    _mirror(tmp_path, "proj", "wf1", "in movement")
    wf = _mock_workflow("in movement")

    with mock.patch.object(workflow_routes, "VWorkflow") as vwf:
        vwf.create.return_value = wf
        r = _app(workflow_routes.bp).test_client().get(
            "/delete-workflow/proj/wf1")

    assert r.status_code == 409
    assert "running" in r.get_json()["error"]
    wf.delete_workspace.assert_not_called()


def test_delete_workflow_backend_failure_500(monkeypatch, tmp_path):
    """A backend failure surfaces as a 500 with the error message."""
    from Yuki.server.routes import workflow as workflow_routes
    monkeypatch.setenv("HOME", str(tmp_path))
    _mirror(tmp_path, "proj", "wf1", "finished")
    wf = _mock_workflow("finished")
    wf.delete_workspace.side_effect = OSError("ssh down")

    with mock.patch.object(workflow_routes, "VWorkflow") as vwf:
        vwf.create.return_value = wf
        r = _app(workflow_routes.bp).test_client().get(
            "/delete-workflow/proj/wf1")

    assert r.status_code == 500
    assert "ssh down" in r.get_json()["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 4 FAIL — `AttributeError: module 'Yuki.server.routes.workflow' has no attribute 'VWorkflow'` (the route and its imports do not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `Yuki/server/routes/workflow.py`, replace the imports block:

```python
"""
Workflow management routes for starting, stopping, and monitoring workflows.
"""
import os
from flask import Blueprint, request, jsonify
from Yuki.kernel.impression_storage import ImpressionStorage
from Yuki.kernel.vworkflow import VWorkflow
from Yuki.kernel.status_constants import IN_MOVEMENT, translate_to_musical

bp = Blueprint('workflow', __name__)
```

Append the route at the end of the file:

```python
@bp.route("/delete-workflow/<project_uuid>/<workflow_uuid>", methods=['GET'])
def delete_workflow(project_uuid, workflow_uuid):
    """Free the runner-side workspace of a workflow (all backends).

    The local Workflows mirror is always kept; only the runner-side
    workspace is removed.
    """
    workflow_dir = os.path.join(os.environ["HOME"], ".Yuki", "Workflows",
                                project_uuid, workflow_uuid)
    if not os.path.isdir(workflow_dir):
        return jsonify({"error": f"workflow '{workflow_uuid}' not found"}), 404

    workflow = VWorkflow.create(project_uuid, [], workflow_uuid)
    if translate_to_musical(workflow.status()) == IN_MOVEMENT:
        return jsonify({"error": "workflow is running; kill it first"}), 409
    try:
        workflow.delete_workspace()
    except Exception as e:  # pylint: disable=broad-exception-caught
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "deleted",
                    "project_uuid": project_uuid,
                    "workflow": workflow_uuid,
                    "backend_type": workflow.backend_type()})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/workflow.py UnitTest/test_delete_workflow.py
git commit -m "feat(server): add /delete-workflow route with running guard"
```

---

### Task 6: Deprecate `/homekeep/<project_uuid>` (410)

**Files:**
- Modify: `Yuki/server/routes/status.py` (replace the existing `homekeep` route, which currently iterates workflows and calls `workflow.homekeep()`)
- Test: `UnitTest/test_delete_workflow.py`

**Interfaces:**
- Produces: `GET /homekeep/<project_uuid>` returns `410` with `{"error": ...}` pointing at `/delete-workflow`. It never constructs a workflow object.

- [ ] **Step 1: Write the failing test**

Append to `UnitTest/test_delete_workflow.py`:

```python
def test_homekeep_is_deprecated():
    """/homekeep returns 410 and points at delete-workflow."""
    from Yuki.server.routes import status as status_routes
    r = _app(status_routes.bp).test_client().get("/homekeep/proj")
    assert r.status_code == 410
    assert "delete-workflow" in r.get_json()["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_delete_workflow.py::test_homekeep_is_deprecated -q`
Expected: FAIL — `assert 200 == 410` (the old route returns "ok" with status 200).

- [ ] **Step 3: Write minimal implementation**

In `Yuki/server/routes/status.py`, replace the whole `homekeep` route:

```python
@bp.route("/homekeep/<project_uuid>", methods=['GET'])
def homekeep(project_uuid):
    """Deprecated: collect results, then free the workspace explicitly."""
    del project_uuid  # kept for route compatibility
    return jsonify({
        "error": ("homekeep is outdated; collect results then free the "
                  "workspace with /delete-workflow/<project>/<workflow>")
    }), 410
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_delete_workflow.py -q`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/status.py UnitTest/test_delete_workflow.py
git commit -m "feat(server): deprecate /homekeep in favor of delete-workflow"
```

---

### Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest UnitTest/ -q`
Expected: all tests pass (384 + 11 new = 395), no failures.

- [ ] **Step 2: Run pylint on every changed file**

Run:
```bash
pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" \
  Yuki/kernel/vworkflow.py Yuki/kernel/ssh_workflow.py Yuki/kernel/native_workflow.py \
  Yuki/kernel/reana_workflow.py Yuki/server/routes/workflow.py Yuki/server/routes/status.py \
  UnitTest/test_delete_workflow.py
```
Expected: no new warnings (the files carry their pre-existing warnings, if any, with the same count as before this feature).

- [ ] **Step 3: Final review of the diff**

Run: `git diff HEAD~6 --stat` (adjust the number to the commits made) and skim that only the files listed in the spec's "Files touched" table changed, plus `UnitTest/test_delete_workflow.py`.
