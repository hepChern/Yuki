# Remote Data Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `register-data` (SSH-runner data registered into a Yuki-managed staging area, MD5-hashed remotely, background job + polling) and rename the data commands to directional verbs (`upload-data`, `attach-data`).

**Architecture:** Yuki gains a `remote_data` blueprint (`POST /register-remote-data` returns a job id; `GET /register-remote-data/<job_id>` reads job state from `$YUKIDIR/register-jobs/`) and a Celery task that runs hashing → copying → impression synthesis over SSH. The impression replicates `yuki-create-data`'s layout plus a `remote.json` marker; SSH workflows stage such data by remote-local `cp` instead of SFTP; submit validates runner binding. Celebi adds a polling `register-data` command and renames `send`→`upload-data`, `use-data`→`attach-data`.

**Tech Stack:** Python 3.8+, Flask, Celery, paramiko (ssh), `CelebiChrono` utils (`csys`, `metadata`, `VImpression`), unittest/pytest with heavy mocking.

**Spec:** `docs/superpowers/specs/2026-08-14-remote-data-registration-design.md` (Yuki repo)

## Global Constraints

- Two repos: **Yuki** at `/Users/wave/workdir/Celebi/Yuki`, **Celebi** at `/Users/wave/workdir/Celebi/Celebi`. Commits go to the repo being changed; both currently on their default branches (Yuki `main`, Celebi `master`), clean.
- Absolute imports from package root; Python 3.8-compatible syntax.
- Tests never touch real `~/.Yuki` / `~/.celebi` — temp dirs, `monkeypatch` env (`YUKIDIR`, `HOME`), mocks.
- MD5 semantics MUST equal `CelebiChrono.utils.file_utils.dir_md5` (walk, exclude dot-names, sorted dirs/files, cumulative hash of per-file md5 hex strings) — same data must yield the same uuid via `send`/`register-data`.
- Impression synthesis must replicate `Yuki/cli/yuki_create_data.py` (canonical rawdata task → `VImpression().generate_imp_uuid(project_uuid, tmp_dir, [])`; layout `contents/` + `config.json` + `status.json` with `"pending"`).
- Old command names `send` / `use-data` are removed entirely (no aliases); kernel methods (`VTask.send`, `InputManager.send`) do NOT change.
- `register-data` requires an ssh runner; native data should use `upload-data`.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Yuki tests: `python -m pytest UnitTest/<file> -v` from the Yuki repo root. Celebi tests: `python -m unittest UnitTest.<module> -v` from the Celebi repo root (some suites need `cd UnitTest` first due to `import prepare`).

---

## Part 1 — Yuki

### Task 1: Remote MD5 script + remote copy command builder

**Files:**
- Create: `Yuki/kernel/remote_data_ops.py`
- Test: `UnitTest/test_remote_data_ops.py` (create)

**Interfaces:**
- Produces (consumed by Tasks 2–5):
  - `REMOTE_MD5_SCRIPT: str` — inline python3 script implementing `dir_md5` semantics.
  - `remote_md5_command(remote_path: str) -> str` — shell command: `python3 -c <script> <path>`.
  - `build_remote_fast_copy_command(src: str, dst: str) -> str` — mkdir + reflink→hardlink→rsync→copy chain.
  - `_yuki_dir() -> str` — `expanduser(YUKIDIR env or "~/.Yuki")`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for remote data operation helpers."""
import os
import shutil
import subprocess
from unittest import mock

from CelebiChrono.utils.file_utils import dir_md5
from Yuki.kernel.remote_data_ops import (
    REMOTE_MD5_SCRIPT, remote_md5_command, build_remote_fast_copy_command,
    _yuki_dir,
)


def _fixture_tree(root):
    """Nested tree with hidden files, matching dir_md5's exclude rules."""
    os.makedirs(os.path.join(root, "sub", "deep"))
    os.makedirs(os.path.join(root, ".hidden_dir"))
    with open(os.path.join(root, "a.txt"), "w") as f:
        f.write("alpha")
    with open(os.path.join(root, "sub", "b.txt"), "w") as f:
        f.write("beta")
    with open(os.path.join(root, "sub", "deep", "c.txt"), "w") as f:
        f.write("gamma")
    with open(os.path.join(root, ".secret"), "w") as f:
        f.write("hidden file content")
    with open(os.path.join(root, ".hidden_dir", "d.txt"), "w") as f:
        f.write("hidden dir content")


def test_remote_md5_matches_dir_md5_semantics(tmp_path):
    fixture = tmp_path / "data"
    _fixture_tree(str(fixture))
    expected = dir_md5(str(fixture))

    result = subprocess.run(
        ["python3", "-c", REMOTE_MD5_SCRIPT, str(fixture)],
        capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_remote_md5_command_quotes_args():
    cmd = remote_md5_command("/data/my dir/with spaces")
    assert cmd.startswith("python3 -c ")
    assert "'/data/my dir/with spaces'" in cmd


def test_fast_copy_command_chain():
    cmd = build_remote_fast_copy_command("/src dir", "/dst dir")
    assert "mkdir -p '/dst dir'" in cmd
    assert "cp -a --reflink=auto '/src dir'/." in cmd
    assert "cp -al '/src dir'/." in cmd
    assert "rsync -a '/src dir'/" in cmd
    assert "cp -r '/src dir'/." in cmd


def test_yuki_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path / "custom"))
    assert _yuki_dir() == str(tmp_path / "custom")
    monkeypatch.delenv("YUKIDIR")
    with mock.patch.dict(os.environ, {"HOME": str(tmp_path)}):
        assert _yuki_dir() == os.path.join(str(tmp_path), ".Yuki")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_remote_data_ops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Yuki.kernel.remote_data_ops'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Remote-side data operations for register-data.

Helpers that build shell commands executed ON an ssh runner, plus the
local Yuki-side storage paths for registration job state.
"""
import os
import shlex

REMOTE_MD5_SCRIPT = r'''
import hashlib, os, sys

def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def dir_md5(root):
    total = hashlib.md5()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        files = [f for f in files if not f.startswith(".")]
        dirs.sort()
        files.sort()
        for name in files:
            total.update(md5sum(os.path.join(cur, name)).encode("utf-8"))
    return total.hexdigest()

print(dir_md5(sys.argv[1]))
'''


def _yuki_dir():
    """Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def remote_md5_command(remote_path):
    """SSH command computing the dir md5 on the remote host."""
    return f"python3 -c {shlex.quote(REMOTE_MD5_SCRIPT)} {shlex.quote(remote_path)}"


def build_remote_fast_copy_command(src, dst):
    """Copy src into dst on the remote host, fastest mechanism first.

    Mirrors yuki_create_data.fast_copy_tree: reflink -> hardlink ->
    rsync -> plain copy.
    """
    return (
        f"mkdir -p {shlex.quote(dst)} && "
        f"(cp -a --reflink=auto {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"cp -al {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"rsync -a {shlex.quote(src)}/ {shlex.quote(dst)}/ || "
        f"cp -r {shlex.quote(src)}/. {shlex.quote(dst)}/)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_remote_data_ops.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/remote_data_ops.py UnitTest/test_remote_data_ops.py
git commit -m "feat(kernel): remote md5 script and fast-copy command builders

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Job-state store + register-remote-data routes

**Files:**
- Modify: `Yuki/kernel/remote_data_ops.py` (append job-state + dedup helpers)
- Create: `Yuki/server/routes/remote_data.py`
- Modify: `Yuki/server/app.py` (register blueprint after line 48)
- Test: `UnitTest/test_remote_data_routes.py` (create)

**Interfaces:**
- Consumes: Task 1 (`_yuki_dir`).
- Produces (used by Tasks 3 and 7):
  - `write_job_state(yuki_dir, job_id, state: dict) -> None`
  - `read_job_state(yuki_dir, job_id) -> dict|None`
  - `find_existing_registration(yuki_dir, runner_id, remote_path) -> dict|None` — scans `Storage/*/*/remote.json`; hit returns `{"result": {"uuid": <md5>, "impression_uuid": <imp>}}`.
  - `find_inflight_job(yuki_dir, runner_id, remote_path) -> str|None` — scans `register-jobs/`; hit returns job_id.
  - Routes: `POST /register-remote-data` (form/JSON `runner`, `remote_path`, `project_uuid`, optional `descriptor`) → `{"job_id"}` or `{"error"}` (400/404) or existing-result; `GET /register-remote-data/<job_id>` → job state JSON, 404 `{"error": "job not found"}`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for register-remote-data routes and job state."""
import json
import os
import tempfile
from unittest import mock

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.kernel import remote_data_ops
from Yuki.server.routes import remote_data as remote_data_routes


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _temp_config(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    config_obj = mock.MagicMock()
    config_obj.config_path = str(tmp_path / "config.json")
    config_obj.get_config_file.return_value = ConfigFile(config_obj.config_path)
    monkeypatch.setattr(remote_data_routes, "config", config_obj)
    return config_obj


def _register_runner(config_obj, name="cluster", backend="ssh"):
    runner_id = "r-uuid"
    data = {"runners": [name], "runners_id": {name: runner_id},
            "backend_types": {runner_id: backend}}
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return runner_id


def test_job_state_roundtrip(tmp_path):
    yuki_dir = tmp_path
    assert remote_data_ops.read_job_state(yuki_dir, "j1") is None
    remote_data_ops.write_job_state(yuki_dir, "j1",
                                    {"status": "hashing", "result": None,
                                     "error": None, "runner_id": "r1",
                                     "remote_path": "/p"})
    state = remote_data_ops.read_job_state(yuki_dir, "j1")
    assert state["status"] == "hashing"
    assert state["runner_id"] == "r1"


def test_find_existing_registration(tmp_path):
    yuki_dir = tmp_path
    imp_dir = yuki_dir / "Storage" / "proj" / "imp-123"
    os.makedirs(imp_dir / "contents")
    with open(imp_dir / "contents" / "celebi.yaml", "w", encoding="utf-8") as f:
        f.write("environment: rawdata\nuuid: abcdef\ndescriptor: d\n")
    marker = ConfigFile(str(imp_dir / "remote.json"))
    marker.write_variable("host_runner_id", "r1")
    marker.write_variable("source_path", "/src/data")
    marker.write_variable("remote_path", "/remote/imp")

    hit = remote_data_ops.find_existing_registration(str(yuki_dir), "r1", "/src/data")
    assert hit == {"result": {"uuid": "abcdef", "impression_uuid": "imp-123"}}
    assert remote_data_ops.find_existing_registration(
        str(yuki_dir), "r1", "/other") is None


def test_find_inflight_job(tmp_path):
    remote_data_ops.write_job_state(
        str(tmp_path), "job-9",
        {"status": "hashing", "result": None, "error": None,
         "runner_id": "r1", "remote_path": "/p"})
    assert remote_data_ops.find_inflight_job(str(tmp_path), "r1", "/p") == "job-9"
    assert remote_data_ops.find_inflight_job(str(tmp_path), "r1", "/x") is None


def test_register_remote_data_starts_job(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj", "descriptor": "mydata"})
    assert r.status_code == 200
    job_id = r.get_json()["job_id"]
    task.apply_async.assert_called_once()
    state = remote_data_ops.read_job_state(str(tmp_path), job_id)
    assert state["status"] == "hashing"
    assert state["remote_path"] == "/src/data"


def test_register_remote_data_unknown_runner(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    r = _app(remote_data_routes.bp).test_client().post(
        "/register-remote-data",
        json={"runner": "ghost", "remote_path": "/p", "project_uuid": "proj"})
    assert r.status_code == 404


def test_register_remote_data_non_ssh_runner(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj, name="local", backend="native")
    r = _app(remote_data_routes.bp).test_client().post(
        "/register-remote-data",
        json={"runner": "local", "remote_path": "/p", "project_uuid": "proj"})
    assert r.status_code == 400
    assert "ssh" in r.get_json()["error"]


def test_register_remote_data_idempotent(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    imp_dir = tmp_path / "Storage" / "proj" / "imp-123"
    os.makedirs(imp_dir / "contents")
    with open(imp_dir / "contents" / "celebi.yaml", "w", encoding="utf-8") as f:
        f.write("environment: rawdata\nuuid: abcdef\ndescriptor: d\n")
    marker = ConfigFile(str(imp_dir / "remote.json"))
    marker.write_variable("host_runner_id", "r-uuid")
    marker.write_variable("source_path", "/src/data")
    marker.write_variable("remote_path", "/remote/imp")
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj"})
    task.apply_async.assert_not_called()
    assert r.get_json()["result"]["uuid"] == "abcdef"


def test_register_remote_data_status(monkeypatch, tmp_path):
    _temp_config(monkeypatch, tmp_path)
    remote_data_ops.write_job_state(
        str(tmp_path), "job-9",
        {"status": "copying", "result": None, "error": None,
         "runner_id": "r1", "remote_path": "/p"})
    r = _app(remote_data_routes.bp).test_client().get(
        "/register-remote-data/job-9")
    assert r.get_json()["status"] == "copying"
    r2 = _app(remote_data_routes.bp).test_client().get(
        "/register-remote-data/ghost")
    assert r2.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_remote_data_routes.py -v`
Expected: FAIL — `No module named 'Yuki.server.routes.remote_data'`

- [ ] **Step 3: Write minimal implementation**

Append to `Yuki/kernel/remote_data_ops.py`:

```python
import json  # add to top imports

JOBS_DIR_NAME = "register-jobs"


def _jobs_dir(yuki_dir):
    return os.path.join(yuki_dir, JOBS_DIR_NAME)


def write_job_state(yuki_dir, job_id, state):
    """Persist a registration job's state to $YUKIDIR/register-jobs/<id>.json."""
    os.makedirs(_jobs_dir(yuki_dir), exist_ok=True)
    with open(os.path.join(_jobs_dir(yuki_dir), f"{job_id}.json"),
              "w", encoding="utf-8") as f:
        json.dump(state, f)


def read_job_state(yuki_dir, job_id):
    """Read a registration job's state, or None."""
    path = os.path.join(_jobs_dir(yuki_dir), f"{job_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _impression_md5(imp_dir):
    from CelebiChrono.utils import metadata
    yaml_file = metadata.YamlFile(os.path.join(imp_dir, "contents", "celebi.yaml"))
    return yaml_file.read_variable("uuid", "")


def find_existing_registration(yuki_dir, runner_id, remote_path):
    """Return the completed registration for (runner, path), else None."""
    storage = os.path.join(yuki_dir, "Storage")
    if not os.path.isdir(storage):
        return None
    for proj in os.listdir(storage):
        proj_dir = os.path.join(storage, proj)
        if not os.path.isdir(proj_dir):
            continue
        for imp in os.listdir(proj_dir):
            marker_path = os.path.join(proj_dir, imp, "remote.json")
            if not os.path.exists(marker_path):
                continue
            marker = ConfigFile(marker_path)
            if marker.read_variable("host_runner_id", "") == runner_id and \
                    marker.read_variable("source_path", "") == remote_path:
                return {"result": {
                    "uuid": _impression_md5(os.path.join(proj_dir, imp)),
                    "impression_uuid": imp,
                }}
    return None


def find_inflight_job(yuki_dir, runner_id, remote_path):
    """Return the job id of an in-flight registration for (runner, path)."""
    jobs_dir = _jobs_dir(yuki_dir)
    if not os.path.isdir(jobs_dir):
        return None
    for name in os.listdir(jobs_dir):
        if not name.endswith(".json"):
            continue
        state = read_job_state(yuki_dir, name[:-5])
        if state is None:
            continue
        if state.get("runner_id") == runner_id and \
                state.get("remote_path") == remote_path and \
                state.get("status") not in ("done", "failed"):
            return name[:-5]
    return None
```

New `Yuki/server/routes/remote_data.py`:

```python
"""
Remote data registration routes.

POST /register-remote-data            start a registration job (returns job_id)
GET  /register-remote-data/<job_id>   poll job state
"""
import os
from flask import Blueprint, request, jsonify
from CelebiChrono.utils import csys
from CelebiChrono.utils.metadata import ConfigFile
from ...kernel import remote_data_ops
from ..config import config
from ..tasks import task_register_remote_data  # defined in Task 3; see note below

bp = Blueprint('remote_data', __name__)

# NOTE for the implementer: until Task 3 lands, this import fails. To keep
# this task self-contained, define a module-level stub first:
#   task_register_remote_data = None
# and replace it with the real import in Task 3's commit (Task 3's test
# suite fails otherwise). The route tests mock this attribute either way.


@bp.route("/register-remote-data", methods=['POST'])
def register_remote_data():
    """Start a remote data registration job."""
    data = request.get_json(silent=True) or request.form
    runner = data.get("runner", "")
    remote_path = data.get("remote_path", "")
    project_uuid = data.get("project_uuid", "")
    if not (runner and remote_path and project_uuid):
        return jsonify({"error": "missing required field: runner/remote_path/project_uuid"}), 400

    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    if runner not in runners_id:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404
    runner_id = runners_id[runner]
    backend_types = config_file.read_variable("backend_types", {})
    if backend_types.get(runner_id, "reana") != "ssh":
        return jsonify({"error": "register-data requires an ssh runner; "
                                 "native data should use upload-data"}), 400

    descriptor = data.get("descriptor") or os.path.basename(
        os.path.normpath(remote_path))

    yuki_dir = remote_data_ops._yuki_dir()
    existing = remote_data_ops.find_existing_registration(
        yuki_dir, runner_id, remote_path)
    if existing:
        return jsonify(existing)
    inflight = remote_data_ops.find_inflight_job(yuki_dir, runner_id, remote_path)
    if inflight:
        return jsonify({"job_id": inflight})

    job_id = csys.generate_uuid()
    remote_data_ops.write_job_state(yuki_dir, job_id, {
        "status": "hashing", "result": None, "error": None,
        "runner_id": runner_id, "remote_path": remote_path,
    })
    task_register_remote_data.apply_async(
        args=[job_id, runner_id, remote_path, project_uuid, descriptor])
    return jsonify({"job_id": job_id})


@bp.route("/register-remote-data/<job_id>", methods=['GET'])
def register_remote_data_status(job_id):
    """Poll a registration job's state."""
    state = remote_data_ops.read_job_state(remote_data_ops._yuki_dir(), job_id)
    if state is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(state)
```

In `Yuki/server/app.py` after the booking registration (line 48):

```python
    flask_app.register_blueprint(remote_data.bp)
```

with the import `from .routes import remote_data` added to the routes import block.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_remote_data_routes.py UnitTest/test_remote_data_ops.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/remote_data_ops.py Yuki/server/routes/remote_data.py Yuki/server/app.py UnitTest/test_remote_data_routes.py
git commit -m "feat(server): register-remote-data routes with job state and idempotency

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Celery registration job (hashing → copying → registering)

**Files:**
- Modify: `Yuki/kernel/remote_data_ops.py` (append `register_remote_data_job` + `synthesize_impression`)
- Modify: `Yuki/server/tasks.py` (add `task_register_remote_data`; replace the route's stub import with the real one)
- Modify: `Yuki/server/routes/remote_data.py` (swap stub for real import, per Task 2 note)
- Test: `UnitTest/test_remote_data_job.py` (create)

**Interfaces:**
- Consumes: Task 1 (`remote_md5_command`, `build_remote_fast_copy_command`), Task 2 (`write_job_state`), `runner_config.get_ssh_settings` (runner-management helpers), `Yuki.cli.yuki_create_data` (`create_canonical_rawdata_task`, `build_impression_config`), `CelebiChrono.kernel.vimpression.VImpression`.
- Produces:
  - `register_remote_data_job(runner_id, remote_path, project_uuid, descriptor, update) -> dict` — `update(state: dict)` is a progress callback; returns `{"uuid": <md5>, "impression_uuid": ..., "descriptor": ...}`.
  - `synthesize_impression(project_uuid, impression_uuid, data_md5, descriptor, runner_id, source_path, managed_dir) -> None`.
  - `@celeryapp.task task_register_remote_data(job_id, runner_id, remote_path, project_uuid, descriptor)`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the register-remote-data celery job."""
import json
import os
from unittest import mock

from CelebiChrono.utils.file_utils import dir_md5
from Yuki.kernel import remote_data_ops


class FakeSsh:
    """Records commands; exec returns (out, err, code)."""
    def __init__(self, md5_out):
        self.md5_out = md5_out
        self.commands = []
        self.made_dirs = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def mkdir_p(self, path):
        self.made_dirs.append(path)

    def exec(self, command, timeout=None):
        self.commands.append(command)
        if command.startswith("python3 -c"):
            return self.md5_out, "", 0
        return "", "", 0


def _fixture_data(tmp_path):
    data = tmp_path / "data"
    os.makedirs(data / "sub")
    with open(data / "a.txt", "w") as f:
        f.write("alpha")
    with open(data / "sub" / "b.txt", "w") as f:
        f.write("beta")
    return data


def test_register_remote_data_job_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    data = _fixture_data(tmp_path)
    md5 = dir_md5(str(data))
    fake = FakeSsh(md5)
    updates = []

    with mock.patch("Yuki.kernel.remote_data_ops._SshConnection",
                    return_value=fake):
        result = remote_data_ops.register_remote_data_job(
            "r1", str(data), "proj", "mydata", updates.append)

    assert result["uuid"] == md5
    assert result["descriptor"] == "mydata"

    # stage transitions
    statuses = [u["status"] for u in updates]
    assert statuses[0] == "hashing"
    assert "copying" in statuses
    assert "registering" in statuses

    # hashing command ran
    assert any(c.startswith("python3 -c") for c in fake.commands)
    # copy command targets the managed impressions dir
    copy_cmd = [c for c in fake.commands if c.startswith("mkdir -p")][0]
    assert "/impressions/proj/" in copy_cmd
    assert "cp -a --reflink=auto" in copy_cmd

    # impression synthesis
    imp_dir = tmp_path / "Storage" / "proj" / result["impression_uuid"]
    assert (imp_dir / "remote.json").exists()
    remote_cfg = json.loads((imp_dir / "remote.json").read_text())
    assert remote_cfg["host_runner_id"] == "r1"
    assert remote_cfg["source_path"] == str(data)
    assert remote_cfg["remote_path"].startswith("/tmp/yuki-workflows/impressions/proj/")
    yaml = (imp_dir / "contents" / "celebi.yaml").read_text()
    assert f"uuid: {md5}" in yaml
    status = json.loads((imp_dir / "status.json").read_text())
    assert status["status"] == "pending"


def test_register_remote_data_job_hash_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    fake = FakeSsh("")

    def failing_exec(command, timeout=None):
        fake.commands.append(command)
        if command.startswith("python3 -c"):
            return "", "no such dir", 1
        return "", "", 0

    fake.exec = failing_exec
    updates = []
    with mock.patch("Yuki.kernel.remote_data_ops._SshConnection",
                    return_value=fake):
        with pytest_raises(RuntimeError) as exc:
            remote_data_ops.register_remote_data_job(
                "r1", "/missing", "proj", "d", updates.append)
    assert "md5" in str(exc.value)
    assert updates[0]["status"] == "hashing"


def test_task_register_remote_data_writes_done_state(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    from Yuki.server import tasks
    data = _fixture_data(tmp_path)
    md5 = dir_md5(str(data))
    fake = FakeSsh(md5)
    with mock.patch("Yuki.kernel.remote_data_ops._SshConnection",
                    return_value=fake):
        tasks.task_register_remote_data.run(
            "job-1", "r1", str(data), "proj", "d")
    state = remote_data_ops.read_job_state(str(tmp_path), "job-1")
    assert state["status"] == "done"
    assert state["result"]["uuid"] == md5
    assert state["error"] is None
```

Note: use `import pytest`; add `pytest.raises` usage — the file must import pytest explicitly.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_remote_data_job.py -v`
Expected: FAIL — `AttributeError: module 'Yuki.kernel.remote_data_ops' has no attribute 'register_remote_data_job'`

- [ ] **Step 3: Write minimal implementation**

Append to `Yuki/kernel/remote_data_ops.py` (imports: `import shutil`, `import tempfile` at top; keep paramiko import lazy inside the ssh usage):

```python
def _ssh_settings(runner_id):
    """Merged ssh settings for a runner."""
    from Yuki.kernel import runner_config
    return runner_config.get_ssh_settings(runner_config.open_config(), runner_id)


def _ssh_connection(runner_id):
    """An _SshConnection for the runner (paramiko import stays lazy)."""
    from Yuki.kernel.ssh_workflow import _SshConnection
    settings = _ssh_settings(runner_id)
    return _SshConnection(host=settings.get("host", ""),
                          user=settings.get("user", ""),
                          key_path=settings.get("key_path"),
                          port=settings.get("port", 22))


def synthesize_impression(project_uuid, impression_uuid, data_md5, descriptor,
                          runner_id, source_path, managed_dir):
    """Create the impression record in Yuki Storage (data stays remote).

    Replicates yuki_create_data's layout (contents/ + config.json +
    status.json) and adds remote.json marking the hosting runner.
    """
    from CelebiChrono.utils import metadata
    from Yuki.cli.yuki_create_data import (
        create_canonical_rawdata_task, build_impression_config,
    )

    impression_dir = os.path.join(_yuki_dir(), "Storage",
                                  project_uuid, impression_uuid)
    os.makedirs(impression_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="yuki_register_") as tmp:
        create_canonical_rawdata_task(tmp, descriptor, data_md5)
        contents_dir = os.path.join(impression_dir, "contents")
        if os.path.exists(contents_dir):
            shutil.rmtree(contents_dir)
        shutil.copytree(tmp, contents_dir)
        impression_config = build_impression_config(
            project_uuid, impression_uuid, tmp)

    config_file = metadata.ConfigFile(os.path.join(impression_dir, "config.json"))
    for key, value in impression_config.items():
        config_file.write_variable(key, value)

    status_file = metadata.ConfigFile(os.path.join(impression_dir, "status.json"))
    status_file.write_variable("status", "pending")

    remote_file = metadata.ConfigFile(os.path.join(impression_dir, "remote.json"))
    remote_file.write_variable("host_runner_id", runner_id)
    remote_file.write_variable("source_path", source_path)
    remote_file.write_variable("remote_path", managed_dir)


def register_remote_data_job(runner_id, remote_path, project_uuid, descriptor,
                             update):
    """Run the registration pipeline: hash -> copy -> register.

    ``update(state: dict)`` is called with each progress state.
    """
    from CelebiChrono.kernel.vimpression import VImpression
    from Yuki.cli.yuki_create_data import create_canonical_rawdata_task

    update({"status": "hashing", "result": None, "error": None})
    with _ssh_connection(runner_id) as ssh:
        out, err, code = ssh.exec(remote_md5_command(remote_path), timeout=3600)
        if code != 0:
            raise RuntimeError(f"remote md5 failed: {err or out}")
        data_md5 = out.strip()
        if not data_md5:
            raise RuntimeError("remote md5 returned empty result")

        with tempfile.TemporaryDirectory(prefix="yuki_register_") as tmp:
            create_canonical_rawdata_task(tmp, descriptor, data_md5)
            impression_uuid = VImpression().generate_imp_uuid(
                project_uuid, tmp, [])

        settings = _ssh_settings(runner_id)
        managed_dir = (f"{settings.get('remote_workdir', '/tmp/yuki-workflows')}"
                       f"/impressions/{project_uuid}/{impression_uuid}")
        update({"status": "copying", "result": None, "error": None})
        out, err, code = ssh.exec(
            build_remote_fast_copy_command(remote_path, managed_dir),
            timeout=10800)
        if code != 0:
            raise RuntimeError(f"remote copy failed: {err or out}")

        update({"status": "registering", "result": None, "error": None})
        synthesize_impression(project_uuid, impression_uuid, data_md5,
                              descriptor, runner_id, remote_path, managed_dir)
        return {"uuid": data_md5, "impression_uuid": impression_uuid,
                "descriptor": descriptor}
```

In `Yuki/server/tasks.py` (top imports add `from ..kernel import remote_data_ops`):

```python
@celeryapp.task
def task_register_remote_data(job_id, runner_id, remote_path, project_uuid,
                              descriptor):
    """Register remote data on an ssh runner: hash, copy, register."""
    yuki_dir = remote_data_ops._yuki_dir()

    def update(state):
        remote_data_ops.write_job_state(yuki_dir, job_id, state)

    try:
        result = remote_data_ops.register_remote_data_job(
            runner_id, remote_path, project_uuid, descriptor, update)
        update({"status": "done", "result": result, "error": None})
    except Exception as e:  # pylint: disable=broad-exception-caught
        update({"status": "failed", "result": None,
                "error": str(e) or type(e).__name__})
```

In `Yuki/server/routes/remote_data.py`, replace the stub with the real import (`from ..tasks import task_register_remote_data`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_remote_data_job.py UnitTest/test_remote_data_routes.py UnitTest/test_remote_data_ops.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/remote_data_ops.py Yuki/server/tasks.py Yuki/server/routes/remote_data.py UnitTest/test_remote_data_job.py
git commit -m "feat(server): celery registration job for remote data

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: SSH workflow stages remote-hosted data locally

**Files:**
- Modify: `Yuki/kernel/ssh_workflow.py` (`_upload_files_remote`, input branch ~L320–343)
- Test: `UnitTest/test_ssh_runner_settings.py` (append)

**Interfaces:**
- Consumes: Task 3's `remote.json` marker (`host_runner_id`, `remote_path` under `$HOME/.Yuki/Storage/<project>/<impression>/remote.json` — note this file uses the `HOME`-based path, matching the surrounding staging code).
- Produces: input jobs backed by remote data are staged via one remote `cp` exec instead of SFTP.

- [ ] **Step 1: Write the failing test** (append to `UnitTest/test_ssh_runner_settings.py`)

```python
def test_stage_remote_hosted_input_copies_locally(tmp_path, monkeypatch):
    """Remote-hosted data on the same runner: one remote cp, no SFTP."""
    yuki_dir = tmp_path / ".Yuki"
    marker_dir = yuki_dir / "Storage" / "proj-123" / "imp-abc"
    marker_dir.mkdir(parents=True)
    with open(marker_dir / "remote.json", "w", encoding="utf-8") as f:
        json.dump({"host_runner_id": "m1", "source_path": "/src",
                   "remote_path": "/remote/impressions/proj-123/imp-abc"}, f)
    monkeypatch.setenv("HOME", str(tmp_path))

    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {"ssh_host": "h", "ssh_user": "u"}},
    })
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/workflows/proj-123/wf-456"
    wf.project_uuid = "proj-123"
    wf.uuid = "wf-456"
    wf.machine_id = "m1"
    wf.snakefile_path = "/local/Snakefile"

    fake_job = mock.MagicMock()
    fake_job.files.return_value = []
    fake_job.environment.return_value = "analysis"
    fake_job.is_input = True
    fake_job.short_uuid.return_value = "abc1234"
    fake_job.path = f"{marker_dir.parent}/imp-abc"  # parent = Storage/proj-123
    wf.jobs = [fake_job]

    commands = []

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def mkdir_p(self, path): commands.append(("mkdir", path))
        def exec(self, command, timeout=None):
            commands.append(("exec", command))
            return "", "", 0

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()):
        wf._upload_files_remote()

    execs = [c for kind, c in commands if kind == "exec"]
    copy_cmd = [c for c in execs if "cp -a --reflink=auto" in c]
    assert copy_cmd, f"expected a remote cp, got: {execs}"
    assert "/remote/impressions/proj-123/imp-abc/." in copy_cmd[0]
    assert "impabc1234/stageout" in copy_cmd[0]


def test_stage_remote_hosted_input_wrong_runner_raises(tmp_path, monkeypatch):
    yuki_dir = tmp_path / ".Yuki"
    marker_dir = yuki_dir / "Storage" / "proj-123" / "imp-abc"
    marker_dir.mkdir(parents=True)
    with open(marker_dir / "remote.json", "w", encoding="utf-8") as f:
        json.dump({"host_runner_id": "OTHER-RUNNER", "source_path": "/src",
                   "remote_path": "/remote/impressions/proj-123/imp-abc"}, f)
    monkeypatch.setenv("HOME", str(tmp_path))

    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {"ssh_host": "h", "ssh_user": "u"}},
    })
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/workflows/proj-123/wf-456"
    wf.project_uuid = "proj-123"
    wf.machine_id = "m1"
    wf.snakefile_path = "/local/Snakefile"
    fake_job = mock.MagicMock()
    fake_job.files.return_value = []
    fake_job.environment.return_value = "analysis"
    fake_job.is_input = True
    fake_job.short_uuid.return_value = "abc1234"
    fake_job.path = f"{marker_dir.parent}/imp-abc"
    wf.jobs = [fake_job]

    with mock.patch.object(SshWorkflow, "_ssh", return_value=mock.MagicMock()):
        with pytest.raises(RuntimeError) as exc:
            wf._upload_files_remote()
    assert "another runner" in str(exc.value)
```

Note: add `import pytest` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_ssh_runner_settings.py -v -k "remote_hosted"`
Expected: FAIL — no remote.json branch (the test fails on the wrong behavior/exception)

- [ ] **Step 3: Write minimal implementation**

In `ssh_workflow.py`, add `import shlex` to the imports, then in `_upload_files_remote`, at the start of the `elif job.is_input:` branch:

```python
                elif job.is_input:
                    impression = job.path.split("/")[-1]
                    remote_marker = os.path.join(
                        os.environ["HOME"], ".Yuki", "Storage",
                        self.project_uuid, impression, "remote.json")
                    if os.path.exists(remote_marker):
                        marker_cfg = metadata.ConfigFile(remote_marker)
                        host_runner = marker_cfg.read_variable(
                            "host_runner_id", "")
                        if host_runner != (self.machine_id or ""):
                            raise RuntimeError(
                                f"Data impression {impression} is hosted on "
                                f"another runner ({host_runner}); cannot stage "
                                "remotely")
                        managed_path = marker_cfg.read_variable(
                            "remote_path", "")
                        dst_path = (f"{self.remote_exec_path}/"
                                    f"imp{job.short_uuid()}/stageout")
                        with self._ssh() as ssh:
                            ssh.mkdir_p(dst_path)
                            out, err, code = ssh.exec(
                                f"cp -a --reflink=auto "
                                f"{shlex.quote(managed_path)}/. "
                                f"{shlex.quote(dst_path)}/",
                                timeout=3600)
                            if code != 0:
                                raise RuntimeError(
                                    f"Remote data staging failed: {err or out}")
                        continue
```

(The existing `impression = job.path.split("/")[-1]` line in the branch is replaced by this block; the remainder of the branch — the SFTP staging from Storage — stays unchanged below it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_ssh_runner_settings.py -v`
Expected: all pass (6 tests)

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/ssh_workflow.py UnitTest/test_ssh_runner_settings.py
git commit -m "feat(kernel): ssh workflow stages remote-hosted data via remote cp

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Submit validation — runner binding for remote-hosted data

**Files:**
- Modify: `Yuki/server/tasks.py` (`task_exec_impression` L29–43)
- Test: `UnitTest/test_remote_data_validation.py` (create)

**Interfaces:**
- Consumes: Task 3's `remote.json` marker; `workflow.jobs` with `is_input`, `job_type()`, `path`; `workflow.set_workflow_status`; `job.set_status`.
- Produces: mismatched workflows are marked `failed` before `workflow.run()`; matching workflows proceed.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for submit-time validation of remote-hosted data runner binding."""
import json
import os
from unittest import mock

from Yuki.server import tasks


def _marker(tmp_path, project, impression, host_runner):
    marker_dir = tmp_path / ".Yuki" / "Storage" / project / impression
    marker_dir.mkdir(parents=True)
    with open(marker_dir / "remote.json", "w", encoding="utf-8") as f:
        json.dump({"host_runner_id": host_runner, "source_path": "/src",
                   "remote_path": "/remote/x"}, f)


def _input_job(impression, is_input=True):
    job = mock.MagicMock()
    job.is_input = is_input
    job.job_type.return_value = "analysis"
    job.path = f"/store/{impression}"
    return job


def test_mismatched_runner_marks_workflow_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _marker(tmp_path, "proj", "imp-abc", "runner-A")
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-abc")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    workflow.set_workflow_status.assert_called_once_with("failed")
    workflow.run.assert_not_called()
    args, kwargs = workflow.jobs[0].set_status.call_args
    assert "imp-abc" in args[1]
    assert "collect" in args[1]


def test_matching_runner_proceeds(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _marker(tmp_path, "proj", "imp-abc", "runner-A")
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-abc")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-A")

    workflow.run.assert_called_once()


def test_no_marker_no_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-no-marker")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    workflow.run.assert_called_once()
    workflow.set_workflow_status.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_remote_data_validation.py -v`
Expected: FAIL — validation absent; `run` called in the mismatch case

- [ ] **Step 3: Write minimal implementation**

In `tasks.py`, add `from CelebiChrono.utils.metadata import ConfigFile` (already imports `metadata` — use `metadata.ConfigFile`), and after `workflow = VWorkflow.create(...)` before `workflow.run()`:

```python
    # Validate runner binding for remote-hosted data impressions.
    runners_id = config.read_variable("runners_id", {})
    runner_name = {v: k for k, v in runners_id.items()}.get(machine_uuid, "unknown")

    def _remote_host(impression):
        marker = os.path.join(os.environ["HOME"], ".Yuki", "Storage",
                              project_uuid, impression, "remote.json")
        if not os.path.exists(marker):
            return None
        return metadata.ConfigFile(marker).read_variable("host_runner_id", "")

    violations = []
    for job in workflow.jobs:
        if not job.is_input:
            continue
        impression = job.path.split("/")[-1] if job.path else ""
        host = _remote_host(impression) if impression else None
        if host and host != machine_uuid:
            violations.append((impression, host))

    if violations:
        from ..kernel.status_constants import DISSONANCE
        impression, host = violations[0]
        host_name = {v: k for k, v in runners_id.items()}.get(host, host)
        message = (f"Data impression {impression} is hosted on runner "
                   f"{host_name}. Submit this workflow to {host_name}, "
                   "or move the data via collect (coming later).")
        workflow.set_workflow_status("failed")
        for job in workflow.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            job.set_status(DISSONANCE, message)
        return
```

Note: `config` is the `metadata.ConfigFile` local variable in the function — reuse it. Remove the unused `runner_name` variable if not used (the code above computes `host_name` per violation; drop the `runner_name` line).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_remote_data_validation.py UnitTest/test_remote_data_job.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/tasks.py UnitTest/test_remote_data_validation.py
git commit -m "feat(server): validate runner binding for remote-hosted data at submit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Part 2 — Celebi

### Task 6: Communicator methods for remote data registration

**Files:**
- Modify: `CelebiChrono/kernel/chern_communicator.py` (after `runner_envs`, before the File Operations section)
- Test: `UnitTest/test_cherncommunicator.py` (append methods to `TestChernCommunicator`)

**Interfaces:**
- Produces:
  - `register_remote_data(runner, remote_path, project_uuid, descriptor=None) -> dict` — POST JSON; 200 → response dict (`{"job_id"}` or `{"result"}`); 404/error → `{"error": ...}`; connection failure raises `ConnectionError`.
  - `register_remote_data_status(job_id) -> dict` — GET; 404 → `{"status": "unknown", "error": "job not found"}`.

- [ ] **Step 1: Write the failing test** (append to `UnitTest/test_cherncommunicator.py`)

```python
    @patch("CelebiChrono.kernel.chern_communicator.requests.post")
    def test_register_remote_data(self, mock_post):
        prepare.create_chern_project("demo_genfit_new")
        os.chdir("demo_genfit_new")
        self.comm = ChernCommunicator()
        self.comm.serverurl = MagicMock(return_value="localhost:8080")
        self.comm.project_uuid = "projectuuid"

        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"job_id": "job-1"}))
        result = self.comm.register_remote_data("cluster", "/src/data",
                                                "projectuuid", "mydata")
        self.assertEqual(result, {"job_id": "job-1"})
        mock_post.assert_called_once_with(
            "http://localhost:8080/register-remote-data",
            json={'runner': 'cluster', 'remote_path': '/src/data',
                  'project_uuid': 'projectuuid', 'descriptor': 'mydata'},
            timeout=10)

        # old server / unknown: JSON error surfaces
        mock_post.return_value = MagicMock(
            status_code=404,
            json=MagicMock(return_value={"error": "Runner 'cluster' not found"}))
        result = self.comm.register_remote_data("cluster", "/src/data", "projectuuid")
        self.assertIn("not found", result["error"])
        os.chdir("..")
        prepare.remove_chern_project("demo_genfit_new")
        CHERN_CACHE.__init__()

    @patch("CelebiChrono.kernel.chern_communicator.requests.get")
    def test_register_remote_data_status(self, mock_get):
        prepare.create_chern_project("demo_genfit_new")
        os.chdir("demo_genfit_new")
        self.comm = ChernCommunicator()
        self.comm.serverurl = MagicMock(return_value="localhost:8080")

        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"status": "copying"}))
        self.assertEqual(self.comm.register_remote_data_status("job-1")["status"],
                         "copying")

        mock_get.return_value = MagicMock(status_code=404)
        self.assertEqual(self.comm.register_remote_data_status("job-x")["status"],
                         "unknown")
        os.chdir("..")
        prepare.remove_chern_project("demo_genfit_new")
        CHERN_CACHE.__init__()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wave/workdir/Celebi/Celebi/UnitTest && python -m unittest test_cherncommunicator.TestChernCommunicator.test_register_remote_data test_cherncommunicator.TestChernCommunicator.test_register_remote_data_status`
Expected: FAIL — `AttributeError: 'ChernCommunicator' object has no attribute 'register_remote_data'`

- [ ] **Step 3: Write minimal implementation**

```python
    def register_remote_data(self, runner, remote_path, project_uuid,
                             descriptor=None):
        """ Register data living on an ssh runner (hashing/copy run on Yuki) """
        url = self.serverurl()
        data = {'runner': runner, 'remote_path': remote_path,
                'project_uuid': project_uuid}
        if descriptor:
            data['descriptor'] = descriptor
        try:
            r = requests.post(f"http://{url}/register-remote-data",
                              json=data, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to DITE server: {e}") from e
        if r.status_code != 200:
            try:
                body = r.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and "error" in body:
                return {"error": body["error"]}
            return {"error": f"register failed (HTTP {r.status_code})"}
        return r.json()

    def register_remote_data_status(self, job_id):
        """ Poll a remote data registration job's state """
        url = self.serverurl()
        try:
            r = requests.get(f"http://{url}/register-remote-data/{job_id}",
                             timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to DITE server: {e}") from e
        if r.status_code == 404:
            return {"status": "unknown", "error": "job not found"}
        return r.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wave/workdir/Celebi/Celebi && python -m unittest UnitTest.test_cherncommunicator -v` (or `cd UnitTest && python -m unittest test_cherncommunicator -v`)
Expected: all pass

- [ ] **Step 5: Commit** (Celebi repo)

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/kernel/chern_communicator.py UnitTest/test_cherncommunicator.py
git commit -m "feat(communicator): remote data registration methods

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: register-data shell + CLI with polling

**Files:**
- Modify: `CelebiChrono/interface/shell_modules/object_creation.py` (extract shared pointer-task helper; add `register_data`; rename happens in Task 8 — this task only ADDS)
- Create: CLI command `register-data` in `CelebiChrono/celebi_cli/commands/object_creation.py`; register in `celebi_cli/cli.py`
- Modify: `CelebiChrono/interface/shell.py` (export `register_data`)
- Test: `UnitTest/test_register_data_command.py` (create)

**Interfaces:**
- Consumes: Task 6 communicator methods; `create_rawdata_task` (`CelebiChrono.kernel.vtask`), `VObject`, `csys`, `metadata`.
- Produces:
  - `register_data(runner: str, remote_path: str, descriptor: str = "") -> Message` — starts the job, polls every 3s printing progress lines, on `done` fills/creates the local rawdata pointer task (rawdata-task context → fill; outside → create; non-rawdata task context → error). Returns the outcome Message.
  - `celebi-cli register-data <runner> <remote_path> [--descriptor X]`.

Implementation notes for the implementer:
- Move the nested `_is_rawdata_task` helper in `use_data` to module level (it is reused).
- Extract from `use_data`'s tail (the else-branch creating/updating a pointer task, lines ~292–340) a module-level helper:

```python
def _fill_or_create_pointer_task(project_path, current_obj, descriptor,
                                 data_md5, path_override, origin):
    """Fill an existing rawdata task or create a pointer task (shared tail
    of attach-data and register-data)."""
    message = Message()
    task_path = path_override if path_override else descriptor
    task_path = csys.refine_path(task_path, current_obj.path)
    full_path = os.path.join(current_obj.path, task_path)
    if not os.path.exists(full_path):
        parent_path = os.path.abspath(full_path + "/..")
        object_type = VObject(parent_path).object_type()
        if object_type not in ("directory", "project"):
            message.add("Not allowed to create data task here", "warning")
            return message
        create_rawdata_task(full_path, descriptor, data_md5)
        message.add(f"Created rawdata task at {task_path}", "success")
        return message
    existing = VObject(full_path, project_path)
    if existing.object_type() != "task":
        message.add(f"Path {task_path} exists but is not a task "
                    f"(type: {existing.object_type()})", "error")
        return message
    yaml_path = os.path.join(full_path, "celebi.yaml")
    yaml_file = metadata.YamlFile(yaml_path)
    env = yaml_file.read_variable("environment", "")
    if env != "rawdata":
        message.add(f"Path {task_path} exists but is not a rawdata task "
                    f"(environment: {env})", "error")
        return message
    yaml_file.write_variable("uuid", data_md5)
    yaml_file.write_variable("descriptor", descriptor)
    message.add(f"Updated rawdata task at {task_path} "
                f"({origin}) with new impression data", "success")
    return message
```

  Refactor `use_data` to call `_fill_or_create_pointer_task(project_path, current_obj, descriptor, data_md5, path_override, "use-data")` in its else-branch (update the "use-data: created..." print label accordingly); the rawdata-task update branch stays as-is. Existing behavior must be preserved — the pre-existing tests for use_data (if any) must still pass.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for register-data shell function and CLI command."""
import unittest
from unittest import mock

from CelebiChrono.interface.shell_modules import object_creation
from CelebiChrono.celebi_cli.commands.object_creation import register_data_command


class TestRegisterData(unittest.TestCase):

    def _make_current(self, obj_type="project", path="/proj", env=None):
        current = mock.MagicMock()
        current.object_type.return_value = obj_type
        current.path = path
        current.project_path.return_value = "/proj"
        current.project_uuid.return_value = "proj-uuid"
        if env is not None:
            # rawdata-task check reads celebi.yaml on disk
            current.is_task = obj_type == "task"
        return current

    def test_polls_until_done_and_creates_pointer_task(self):
        current = self._make_current("directory", path="/proj/dir")
        states = iter([
            {"status": "hashing"},
            {"status": "copying"},
            {"status": "done",
             "result": {"uuid": "md5abc", "impression_uuid": "imp-1",
                        "descriptor": "d"}},
        ])
        cc = mock.MagicMock()
        cc.register_remote_data.return_value = {"job_id": "job-1"}
        cc.register_remote_data_status.side_effect = lambda j: next(states)

        with mock.patch.object(object_creation, "MANAGER") as manager, \
                mock.patch.object(object_creation, "ChernCommunicator") as cccls, \
                mock.patch.object(object_creation.time, "sleep"), \
                mock.patch.object(object_creation, "_fill_or_create_pointer_task",
                                  return_value=mock.MagicMock(messages=[])) as fill:
            manager.current_object.return_value = current
            cccls.instance.return_value = cc
            message = object_creation.register_data("cluster", "/src/data", "d")

        fill.assert_called_once_with(
            "/proj", current, "d", "md5abc", "", "register-data")
        self.assertTrue(any("Registered" in str(m) for m in message.messages))

    def test_failed_job_reports_error(self):
        current = self._make_current("project")
        states = iter([{"status": "failed", "error": "remote md5 failed: boom"}])
        cc = mock.MagicMock()
        cc.register_remote_data.return_value = {"job_id": "job-1"}
        cc.register_remote_data_status.side_effect = lambda j: next(states)
        with mock.patch.object(object_creation, "MANAGER") as manager, \
                mock.patch.object(object_creation, "ChernCommunicator") as cccls, \
                mock.patch.object(object_creation.time, "sleep"), \
                mock.patch.object(object_creation, "_fill_or_create_pointer_task") as fill:
            manager.current_object.return_value = current
            cccls.instance.return_value = cc
            message = object_creation.register_data("cluster", "/src/data")
        fill.assert_not_called()
        self.assertTrue(any("boom" in str(m) for m in message.messages))

    def test_server_error_returned(self):
        current = self._make_current("project")
        cc = mock.MagicMock()
        cc.register_remote_data.return_value = {"error": "requires an ssh runner"}
        with mock.patch.object(object_creation, "MANAGER") as manager, \
                mock.patch.object(object_creation, "ChernCommunicator") as cccls:
            manager.current_object.return_value = current
            cccls.instance.return_value = cc
            message = object_creation.register_data("local", "/p")
        self.assertTrue(any("ssh runner" in str(m) for m in message.messages))

    def test_cli_command(self):
        from click.testing import CliRunner
        with mock.patch("CelebiChrono.interface.shell.register_data") as fn:
            result = CliRunner().invoke(register_data_command,
                                        ["cluster", "/src/data",
                                         "--descriptor", "d"])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("cluster", "/src/data", "d")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wave/workdir/Celebi/Celebi/UnitTest && python -m unittest test_register_data_command`
Expected: FAIL — `cannot import name 'register_data_command'` / `AttributeError: module ... has no attribute 'register_data'`

- [ ] **Step 3: Write minimal implementation**

`object_creation.py` (shell): module-level `_is_rawdata_task` (moved from `use_data`), `_fill_or_create_pointer_task` (code above), `use_data` refactored to use it, plus:

```python
def register_data(runner: str, remote_path: str, descriptor: str = "") -> Message:
    """Register data living on an ssh runner into Yuki's managed staging.

    Computes the data MD5 and copies it into the runner's managed
    impressions area (hashing/copying run as a background job on Yuki).
    On success, fills or creates the local rawdata pointer task.
    """
    import time as time_module
    message = Message()
    current_obj = MANAGER.current_object()
    if current_obj is None:
        message.add("No current object selected", "error")
        return message
    project_path = current_obj.project_path()
    if not project_path:
        message.add("No current project selected", "error")
        return message
    if current_obj.object_type() == "task" and \
            not _is_rawdata_task(current_obj.path):
        message.add("Current task is not a rawdata task; run register-data "
                    "from a rawdata task or outside a task", "error")
        return message

    cherncc = ChernCommunicator.instance()
    resp = cherncc.register_remote_data(runner, remote_path,
                                        current_obj.project_uuid(),
                                        descriptor or None)
    if "error" in resp:
        message.add(resp["error"], "error")
        return message
    job_id = resp["job_id"]
    print(f"register-data: job {job_id[:8]}... started on '{runner}'")
    while True:
        state = cherncc.register_remote_data_status(job_id)
        status = state.get("status", "unknown")
        if status == "done":
            result = state["result"]
            message.add(
                f"Registered: md5={result['uuid']} "
                f"impression={result['impression_uuid']}", "success")
            message.append(_fill_or_create_pointer_task(
                project_path, current_obj, result["descriptor"],
                result["uuid"], "", "register-data"))
            return message
        if status == "failed":
            message.add(f"Registration failed: {state.get('error')}", "error")
            return message
        print(f"register-data: {status}...")
        time_module.sleep(3)
```

(Ensure `import time` at module top instead of the inline import if the file already imports time; match existing style.)

CLI `object_creation.py` (celebi_cli):

```python
@click.command(name="register-data")
@click.argument("runner", type=str)
@click.argument("remote_path", type=str)
@click.option("--descriptor", type=str, default="",
              help="Task descriptor (defaults to remote path basename)")
def register_data_command(runner: str, remote_path: str, descriptor: str) -> None:
    """Register data living on an ssh runner (MD5 + managed staging).

    RUNNER is an ssh runner; REMOTE_PATH is a directory on that runner.
    The data is copied into Yuki's managed impressions area on the runner
    and registered as an impression; a local rawdata pointer task is
    created or filled.
    """
    try:
        from CelebiChrono.interface.shell import register_data
        _handle_result(register_data(runner, remote_path, descriptor))
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")
```

`cli.py`: `cli.add_command(object_creation.register_data_command)` after `use_data_command` registration (line 47).

`shell.py`: add `register_data` to the object_creation import list and `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wave/workdir/Celebi/Celebi/UnitTest && python -m unittest test_register_data_command -v`
Expected: 4 passed. Then `cd .. && python -m unittest UnitTest.test_register_data_command -v` if cwd allows.

- [ ] **Step 5: Commit** (Celebi repo)

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/interface/shell_modules/object_creation.py CelebiChrono/celebi_cli/commands/object_creation.py CelebiChrono/celebi_cli/cli.py CelebiChrono/interface/shell.py UnitTest/test_register_data_command.py
git commit -m "feat(cli): register-data with polling and pointer-task creation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Rename wave — upload-data / attach-data (old names removed)

**Files:**
- Modify: `CelebiChrono/celebi_cli/commands/file_operations.py` (`send_command` → `upload_data_command`, name `upload-data`)
- Modify: `CelebiChrono/celebi_cli/commands/object_creation.py` (`use_data_command` → `attach_data_command`, name `attach-data`)
- Modify: `CelebiChrono/celebi_cli/cli.py` (registrations)
- Modify: `CelebiChrono/interface/shell_modules/file_operations.py` (`send` → `upload_data`)
- Modify: `CelebiChrono/interface/shell_modules/object_creation.py` (`use_data` → `attach_data`)
- Modify: `CelebiChrono/interface/shell.py` (import list + `__all__`)
- Modify: `CelebiChrono/interface/chern_shell/commands_advanced.py` (`do_send` → `do_upload_data`)
- Modify: `CelebiChrono/interface/chern_shell/commands_task.py` (`do_use_data` → `do_attach_data`)
- Test: `UnitTest/test_data_command_renames.py` (create)

**Interfaces:**
- Produces: `upload_data(path) -> Message`, `attach_data(impression_uuid, path_override="") -> Message` in shell layer; CLI `upload-data`, `attach-data`; chern_shell `do_upload_data`, `do_attach_data`. Old names gone.
- Kernel (`VTask.send`, `InputManager.send`, `ChernCommunicator` internals) unchanged.

- [ ] **Step 1: Write the failing test**

```python
"""Tests that the renamed data commands exist and old names are gone."""
import unittest
from unittest import mock

from click.testing import CliRunner


class TestDataCommandRenames(unittest.TestCase):

    def test_upload_data_command_exists(self):
        from CelebiChrono.celebi_cli.commands.file_operations import (
            upload_data_command)
        with mock.patch("CelebiChrono.interface.shell.upload_data") as fn:
            result = CliRunner().invoke(upload_data_command, ["/data/dir"])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("/data/dir")

    def test_attach_data_command_exists(self):
        from CelebiChrono.celebi_cli.commands.object_creation import (
            attach_data_command)
        with mock.patch("CelebiChrono.interface.shell.attach_data") as fn:
            result = CliRunner().invoke(attach_data_command, ["imp-uuid"])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("imp-uuid", "")

    def test_old_cli_names_removed(self):
        from CelebiChrono.celebi_cli.cli import cli
        result = CliRunner().invoke(cli, ["send", "/x"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such command", result.output)
        result = CliRunner().invoke(cli, ["use-data", "uuid"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such command", result.output)

    def test_chern_shell_renames(self):
        from CelebiChrono.interface.chern_shell.commands_advanced import (
            AdvancedCommands)
        from CelebiChrono.interface.chern_shell.commands_task import (
            TaskCommands)
        from CelebiChrono.interface.chern_shell import commands_advanced
        from CelebiChrono.interface.chern_shell import commands_task
        adv = AdvancedCommands.__new__(AdvancedCommands)
        task_cmds = TaskCommands.__new__(TaskCommands)
        with mock.patch.object(commands_advanced, "shell") as shell:
            shell.upload_data.return_value = mock.MagicMock(messages=[])
            adv.do_upload_data("/data/dir")
            shell.upload_data.assert_called_once_with("/data/dir")
        with mock.patch.object(commands_task, "shell") as shell:
            shell.attach_data.return_value = mock.MagicMock(messages=[])
            task_cmds.do_attach_data("imp-uuid")
            shell.attach_data.assert_called_once_with("imp-uuid", "")
```

(Verify the actual chern_shell class names — `AdvancedCommands` in `commands_advanced.py` and `TaskCommands` in `commands_task.py` — by reading the files before writing the test; adjust the imports accordingly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wave/workdir/Celebi/Celebi/UnitTest && python -m unittest test_data_command_renames`
Expected: FAIL — ImportError/AttributeError for the new names

- [ ] **Step 3: Write minimal implementation**

For each of the eight files, rename and update docstrings/usage strings:

- `file_operations.py` (cli): `@click.command(name="upload-data")`, `def upload_data_command(path)`, body calls `shell.upload_data`. Docstring: "Upload a local path's data to DITE (registers an impression on the server)."
- `object_creation.py` (cli): `@click.command(name="attach-data")`, `def attach_data_command(impression_uuid, path)`, body calls `shell.attach_data`.
- `cli.py`: replace `file_operations.send_command` → `file_operations.upload_data_command`; `object_creation.use_data_command` → `object_creation.attach_data_command`.
- `shell_modules/file_operations.py`: `def upload_data(path: str) -> Message:` (body unchanged except `current_obj.send(path, ...)` stays — kernel call unchanged); docstring: "Upload a local path to DITE (was send)."
- `shell_modules/object_creation.py`: `def attach_data(impression_uuid: str, path_override: str = "") -> Message:` (body unchanged; update the `print("use-data: ...")` labels to `attach-data`).
- `shell.py`: import list `send` → `upload_data`, `use_data` → `attach_data`; same in `__all__`.
- `chern_shell/commands_advanced.py`: `do_send` → `do_upload_data`, call `shell.upload_data`.
- `chern_shell/commands_task.py`: `do_use_data` → `do_attach_data`, call `shell.attach_data`.

Then run: `grep -rn "use-data\|use_data\|do_send\|\.send_command\|shell.send\b" CelebiChrono/` and fix any remaining user-facing references (kernel `send`/`InputManager` internals are exempt).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wave/workdir/Celebi/Celebi/UnitTest && python -m unittest test_data_command_renames test_register_data_command -v`, then `cd .. && python -m unittest discover UnitTest 2>&1 | tail -3`
Expected: new tests pass; full suite OK

- [ ] **Step 5: Commit** (Celebi repo)

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/celebi_cli/commands/file_operations.py CelebiChrono/celebi_cli/commands/object_creation.py CelebiChrono/celebi_cli/cli.py CelebiChrono/interface/shell_modules/file_operations.py CelebiChrono/interface/shell_modules/object_creation.py CelebiChrono/interface/shell.py CelebiChrono/interface/chern_shell/commands_advanced.py CelebiChrono/interface/chern_shell/commands_task.py UnitTest/test_data_command_renames.py
git commit -m "feat(cli): rename data commands to directional verbs (upload-data, attach-data)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Full suites, lint, manual e2e

**Files:** none (verification only)

- [ ] **Step 1: Yuki full suite**

Run: `cd /Users/wave/workdir/Celebi/Yuki && python -m pytest UnitTest/ -v`
Expected: all pass

- [ ] **Step 2: Celebi full suite**

Run: `cd /Users/wave/workdir/Celebi/Celebi && python -m unittest discover UnitTest -v`
Expected: all pass

- [ ] **Step 3: Pylint both repos**

Run (each repo): `pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" $(git ls-files '*.py')`
Expected: no new warnings on changed files

- [ ] **Step 4: Manual e2e on pkufarm212 (documented in final report)**

```bash
cd /Users/wave/workdir/Celebi/Yuki && docker compose restart
# wait for the server, then from a Celebi demo project:
celebi-cli register-data pkufarm212 /home/zhaomr/workdir/celebi_ssh_runner/testdata --descriptor mydata
#   -> progress: hashing... copying... registering...; then "Registered: md5=... impression=..."
celebi-cli submit pkufarm212            # workflow using the new data task as input
#   -> verify the remote staging used the local cp (check remote logs)
celebi-cli submit local                 # submit the same workflow to a different runner
#   -> expect validation error: "Data impression ... is hosted on runner pkufarm212 ..."
```

- [ ] **Step 5: Fix anything surfaced; commit fixes if made**
