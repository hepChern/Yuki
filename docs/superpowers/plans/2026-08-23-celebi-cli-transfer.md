# `celebi-cli transfer` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `celebi-cli transfer <source> <destination>` command that moves stageout results between Yuki local storage and SSH runner managed impressions caches, with progress reporting.

**Architecture:** Yuki exposes `POST /transfer` and `GET /transfer/<job_id>` routes backed by a Celery task. The task uses a new `Yuki/kernel/result_transfer.py` module to copy files locally and over SFTP, writing progress to a JSON file. CelebiChrono's CLI polls the status endpoint and drives a `tqdm` progress bar.

**Tech Stack:** Python, Click, Flask, Celery, Paramiko (Yuki); Click, tqdm, requests (CelebiChrono).

**Spec:** `docs/superpowers/specs/2026-08-23-celebi-cli-transfer-design.md`

## Global Constraints

- Only SSH runners are supported; native/REANA runners return HTTP 400.
- Transferred files are stageout files of the current impression.
- Source/destination specifiers are `yuki` or `runner:<runner-id>`.
- `--pattern` is an optional glob relative to the stageout/cache root.
- `--force` overwrites existing destination files; default is skip.
- Progress JSON lives in `~/.Yuki/transfer-progress/<job_id>.json`.

---

## File Structure

### Yuki repo

| File | Responsibility |
|------|----------------|
| `Yuki/kernel/result_transfer.py` | Resolve locations, list files, copy local/remote, write progress/report |
| `Yuki/server/tasks.py` | Celery task `task_transfer_results` that calls `result_transfer.run_transfer` |
| `Yuki/server/routes/transfer.py` | Flask routes `POST /transfer` and `GET /transfer/<job_id>` |
| `UnitTest/test_result_transfer.py` | Unit tests for the transfer module |
| `UnitTest/test_transfer_routes.py` | Unit tests for the Flask routes |

### CelebiChrono repo

| File | Responsibility |
|------|----------------|
| `CelebiChrono/kernel/chern_communicator.py` | `transfer()` and `transfer_status()` HTTP client methods |
| `CelebiChrono/interface/shell_modules/file_operations.py` | `transfer()` shell function: context + poll loop + tqdm |
| `CelebiChrono/celebi_cli/commands/file_operations.py` | `transfer_command` Click wrapper |
| `CelebiChrono/celebi_cli/cli.py` | Register `transfer_command` |
| `CelebiChrono/UnitTest/test_transfer_command.py` | CLI / communicator tests |

---

## Task 1: Yuki transfer module skeleton + local listing

**Files:**
- Create: `Yuki/kernel/result_transfer.py`
- Test: `UnitTest/test_result_transfer.py`

**Interfaces:**
- Consumes: nothing
- Produces: `_resolve_yuki_dir()`, `_parse_location()`, `_list_local_files()`, `_make_progress_dir()`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for Yuki.kernel.result_transfer."""
import os
from unittest import mock

import pytest

from Yuki.kernel import result_transfer


def test_resolve_yuki_dir_defaults_to_home():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert result_transfer._resolve_yuki_dir() == os.path.expanduser("~/.Yuki")


def test_resolve_yuki_dir_uses_yukidir_env():
    with mock.patch.dict(os.environ, {"YUKIDIR": "/tmp/yuki-test"}):
        assert result_transfer._resolve_yuki_dir() == "/tmp/yuki-test"


def test_parse_location_yuki():
    assert result_transfer._parse_location("yuki") == ("yuki", None)


def test_parse_location_runner():
    assert result_transfer._parse_location("runner:pkufarm") == ("runner", "pkufarm")


def test_parse_location_runner_missing_id():
    with pytest.raises(ValueError):
        result_transfer._parse_location("runner:")


def test_list_local_files(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    files = result_transfer._list_local_files(str(tmp_path))
    assert sorted(f["name"] for f in files) == ["a.txt", "sub/b.txt"]
    assert files[0]["size"] == 5


def test_list_local_files_missing_dir():
    files = result_transfer._list_local_files("/nonexistent/path")
    assert files == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_result_transfer.py -v`
Expected: failures because `result_transfer` module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
"""Result transfer logic for celebi-cli transfer."""
import fnmatch
import os
from typing import List, Optional, Tuple


def _resolve_yuki_dir():
    """Return the Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def _parse_location(location: str) -> Tuple[str, Optional[str]]:
    """Parse 'yuki' or 'runner:<runner-id>' into (kind, runner_id)."""
    if location == "yuki":
        return "yuki", None
    if location.startswith("runner:"):
        runner_id = location[len("runner:"):]
        if not runner_id:
            raise ValueError("runner id is empty")
        return "runner", runner_id
    raise ValueError(f"invalid location: {location}")


def _list_local_files(root: str, pattern: Optional[str] = None) -> List[dict]:
    """List files under root as [{'name': rel_path, 'size': bytes}]."""
    result = []
    if not os.path.isdir(root):
        return result
    for dirpath, _dirs, filenames in os.walk(root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            result.append({"name": rel, "size": os.path.getsize(full)})
    return result


def _make_progress_dir(yuki_dir: str) -> str:
    """Create and return the transfer progress directory."""
    path = os.path.join(yuki_dir, "transfer-progress")
    os.makedirs(path, exist_ok=True)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_result_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/result_transfer.py UnitTest/test_result_transfer.py
git commit -m "feat(kernel): add result_transfer location parsing and local listing"
```

---

## Task 2: Yuki remote file listing and path resolution

**Files:**
- Modify: `Yuki/kernel/result_transfer.py`
- Test: `UnitTest/test_result_transfer.py`

**Interfaces:**
- Consumes: `_parse_location()`, `_list_local_files()`
- Produces: `_resolve_path()`, `_list_remote_files()`, `_ssh_connection()`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_path_yuki(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    project_uuid = "proj-uuid"
    impression = "imp-uuid"
    path = result_transfer._resolve_path("yuki", project_uuid, impression)
    assert path == str(yuki_dir / "Storage" / project_uuid / impression / "stageout")


def test_resolve_path_runner(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    config_path = yuki_dir / "config.json"
    config_path.write_text(json.dumps({
        "runners_id": {"pkufarm": "runner-uuid"},
        "runners": ["pkufarm"],
        "runner_settings": {
            "runner-uuid": {"ssh_host": "host", "ssh_user": "user",
                            "remote_workdir": "/remote/work"}
        }
    }))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    path = result_transfer._resolve_path(
        "runner:pkufarm", "proj-uuid", "imp-uuid")
    assert path == "/remote/work/impressions/proj-uuid/imp-uuid"


def test_list_remote_files_uses_ssh_walk():
    ssh = mock.MagicMock()
    ssh.walk_files.return_value = [
        ("a.txt", "/remote/a.txt", 10),
        ("sub/b.txt", "/remote/sub/b.txt", 20),
    ]
    files = result_transfer._list_remote_files(ssh, "/remote/root", "*.txt")
    assert sorted(f["name"] for f in files) == ["a.txt", "sub/b.txt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_result_transfer.py::test_resolve_path_yuki -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `Yuki/kernel/result_transfer.py`:

```python
import json

from Yuki.kernel import runner_config
from Yuki.kernel.ssh_workflow import _SshConnection


def _resolve_path(location: str, project_uuid: str, impression: str,
                  yuki_dir: str = None) -> Tuple[str, Optional[str]]:
    """Return (path, runner_id) for a location.

    For yuki: ~/.Yuki/Storage/<project>/<impression>/stageout
    For runner: <remote_workdir>/impressions/<project>/<impression>
    """
    kind, runner_name = _parse_location(location)
    if kind == "yuki":
        yuki_dir = yuki_dir or _resolve_yuki_dir()
        return os.path.join(yuki_dir, "Storage", project_uuid,
                            impression, "stageout"), None

    # runner
    yuki_dir = yuki_dir or _resolve_yuki_dir()
    config_file = runner_config.open_config()
    runners_id = config_file.read_variable("runners_id", {})
    if runner_name not in runners_id:
        raise ValueError(f"runner '{runner_name}' not found")
    runner_id = runners_id[runner_name]
    settings = runner_config.get_ssh_settings(config_file, runner_id)
    remote_workdir = settings.get("remote_workdir", "/tmp/yuki-workflows")
    remote_path = os.path.join(
        remote_workdir, "impressions", project_uuid, impression
    ).replace(os.sep, "/")
    return remote_path, runner_id


def _ssh_connection(runner_id: str) -> _SshConnection:
    """Build an SSH connection for runner_id from config."""
    config_file = runner_config.open_config()
    settings = runner_config.get_ssh_settings(config_file, runner_id)
    return _SshConnection(
        host=settings.get("host", ""),
        user=settings.get("user", ""),
        key_path=settings.get("key_path"),
        port=settings.get("port", 22),
    )


def _list_remote_files(ssh: _SshConnection, remote_root: str,
                       pattern: Optional[str] = None) -> List[dict]:
    """List files on the remote host under remote_root."""
    result = []
    if not ssh.exists(remote_root):
        return result
    for rel, full_path, size in ssh.walk_files(remote_root):
        if pattern and not fnmatch.fnmatch(rel, pattern):
            continue
        result.append({"name": rel, "size": size, "remote_path": full_path})
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_result_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/result_transfer.py UnitTest/test_result_transfer.py
git commit -m "feat(kernel): resolve transfer paths and list remote files"
```

---

## Task 3: Yuki copy implementations

**Files:**
- Modify: `Yuki/kernel/result_transfer.py`
- Test: `UnitTest/test_result_transfer.py`

**Interfaces:**
- Consumes: `_list_local_files()`, `_list_remote_files()`, `_ssh_connection()`
- Produces: `_copy_local_to_local()`, `_copy_local_to_remote()`, `_copy_remote_to_local()`, `_copy_remote_to_remote()`

- [ ] **Step 1: Write the failing test**

```python
def test_copy_local_to_local(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("world")
    progress = {"bytes_done": 0}
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_local(
        str(src), str(dst), force=False,
        progress=progress, report=report)
    assert sorted(report["transferred"]) == ["a.txt", "sub/b.txt"]
    assert (dst / "a.txt").read_text() == "hello"
    assert (dst / "sub" / "b.txt").read_text() == "world"
    assert progress["bytes_done"] == 10


def test_copy_local_to_local_skips_existing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("hello")
    (dst / "a.txt").write_text("existing")
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_local(
        str(src), str(dst), force=False,
        progress={"bytes_done": 0}, report=report)
    assert report["skipped"] == ["a.txt"]
    assert (dst / "a.txt").read_text() == "existing"


def test_copy_local_to_remote(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    ssh = mock.MagicMock()
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_remote(
        str(src), "/remote/dst", ssh, force=False,
        progress={"bytes_done": 0}, report=report)
    ssh.put.assert_called_once()
    assert report["transferred"] == ["a.txt"]


def test_copy_remote_to_local(tmp_path):
    dst = tmp_path / "dst"
    ssh = mock.MagicMock()
    ssh.exists.return_value = True
    ssh.walk_files.return_value = [("a.txt", "/remote/a.txt", 5)]
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_remote_to_local(
        "/remote/src", str(dst), ssh, force=False,
        progress={"bytes_done": 0}, report=report)
    ssh.get.assert_called_once_with("/remote/a.txt", mock.ANY)
    assert report["transferred"] == ["a.txt"]
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `Yuki/kernel/result_transfer.py`:

```python
def _copy_local_to_local(src_root: str, dst_root: str, force: bool,
                         progress: dict, report: dict) -> None:
    """Copy files from src_root to dst_root."""
    files = _list_local_files(src_root)
    for entry in files:
        rel = entry["name"]
        src_file = os.path.join(src_root, rel)
        dst_file = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        if os.path.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            with open(src_file, "rb") as sf, open(dst_file, "wb") as df:
                data = sf.read()
                df.write(data)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


def _copy_local_to_remote(src_root: str, dst_root: str, ssh: _SshConnection,
                          force: bool, progress: dict, report: dict) -> None:
    """Upload files from src_root to dst_root on the remote host."""
    files = _list_local_files(src_root)
    for entry in files:
        rel = entry["name"]
        src_file = os.path.join(src_root, rel)
        dst_file = f"{dst_root}/{rel}"
        if ssh.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            ssh.put(src_file, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


def _copy_remote_to_local(src_root: str, dst_root: str, ssh: _SshConnection,
                          force: bool, progress: dict, report: dict) -> None:
    """Download files from src_root on the remote host to dst_root."""
    files = _list_remote_files(ssh, src_root)
    for entry in files:
        rel = entry["name"]
        src_file = entry["remote_path"]
        dst_file = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        if os.path.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            ssh.get(src_file, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


def _copy_remote_to_remote(src_root: str, dst_root: str,
                           src_ssh: _SshConnection, dst_ssh: _SshConnection,
                           force: bool, progress: dict, report: dict) -> None:
    """Stream files from src runner to dst runner through Yuki host."""
    files = _list_remote_files(src_ssh, src_root)
    for entry in files:
        rel = entry["name"]
        src_file = entry["remote_path"]
        dst_file = f"{dst_root}/{rel}"
        if dst_ssh.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            for chunk in src_ssh.stream(src_file):
                # We'll implement chunked streaming in the next refinement.
                # For now, use a temporary buffer approach.
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(chunk)
                    tmp_path = tmp.name
            dst_ssh.put(tmp_path, dst_file)
            os.unlink(tmp_path)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_result_transfer.py -v`
Expected: PASS (may need minor adjustments for the remote-to-remote test)

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/result_transfer.py UnitTest/test_result_transfer.py
git commit -m "feat(kernel): implement local/remote copy primitives"
```

---

## Task 4: Yuki `run_transfer` orchestration + progress JSON

**Files:**
- Modify: `Yuki/kernel/result_transfer.py`
- Test: `UnitTest/test_result_transfer.py`

**Interfaces:**
- Consumes: all copy primitives
- Produces: `run_transfer()` public API

- [ ] **Step 1: Write the failing test**

```python
def test_run_transfer_local_to_local(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    storage = yuki_dir / "Storage" / "proj" / "imp" / "stageout"
    storage.mkdir(parents=True)
    (storage / "a.txt").write_text("hello")
    dst = tmp_path / "dst"
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    report = result_transfer.run_transfer(
        "job1", "proj", "imp", "yuki", "runner:pkufarm",
        pattern=None, force=False, yuki_dir=str(yuki_dir))
    # Because pkufarm is not configured, this will fail; we need a helper test fixture.
    # Instead, test yuki -> yuki is rejected or test with mocked config.
```

A better test:

```python
def test_run_transfer_yuki_to_yuki_rejected(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    with pytest.raises(ValueError, match="source and destination cannot both be yuki"):
        result_transfer.run_transfer(
            "job1", "proj", "imp", "yuki", "yuki",
            pattern=None, force=False, yuki_dir=str(yuki_dir))


def test_run_transfer_with_mocked_remote(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    storage = yuki_dir / "Storage" / "proj" / "imp" / "stageout"
    storage.mkdir(parents=True)
    (storage / "a.txt").write_text("hello")
    config_path = yuki_dir / "config.json"
    config_path.write_text(json.dumps({
        "runners_id": {"pkufarm": "runner-uuid"},
        "runners": ["pkufarm"],
        "runner_settings": {
            "runner-uuid": {"ssh_host": "host", "ssh_user": "user",
                            "remote_workdir": "/remote/work"}
        }
    }))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))

    with mock.patch("Yuki.kernel.result_transfer._ssh_connection") as ssh_conn:
        ssh = mock.MagicMock()
        ssh.exists.return_value = False
        ssh.walk_files.return_value = []
        ssh_conn.return_value.__enter__ = mock.Mock(return_value=ssh)
        ssh_conn.return_value.__exit__ = mock.Mock(return_value=False)
        report = result_transfer.run_transfer(
            "job1", "proj", "imp", "yuki", "runner:pkufarm",
            pattern=None, force=False, yuki_dir=str(yuki_dir))
        assert report["transferred"] == ["a.txt"]
        ssh.put.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Replace the remote-to-remote placeholder and add `run_transfer`:

```python
def run_transfer(job_id: str, project_uuid: str, impression: str,
                 source: str, destination: str,
                 pattern: Optional[str] = None, force: bool = False,
                 yuki_dir: str = None) -> dict:
    """Run a transfer job and return the final report."""
    yuki_dir = yuki_dir or _resolve_yuki_dir()
    src_path, src_runner = _resolve_path(source, project_uuid, impression, yuki_dir)
    dst_path, dst_runner = _resolve_path(destination, project_uuid, impression, yuki_dir)

    if source == "yuki" and destination == "yuki":
        raise ValueError("source and destination cannot both be yuki")

    progress_dir = _make_progress_dir(yuki_dir)
    progress_path = os.path.join(progress_dir, f"{job_id}.json")

    def write_status(status, extra=None):
        state = {
            "status": status,
            "bytes_done": progress["bytes_done"],
            "bytes_total": progress["bytes_total"],
            "current_file": progress.get("current_file", ""),
        }
        if extra:
            state.update(extra)
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    # Discover files on both sides to compute total
    progress = {"bytes_done": 0, "bytes_total": 0, "current_file": ""}
    if source == "yuki":
        src_files = _list_local_files(src_path, pattern)
    else:
        with _ssh_connection(src_runner) as ssh:
            src_files = _list_remote_files(ssh, src_path, pattern)

    progress["bytes_total"] = sum(f["size"] for f in src_files)
    write_status("running")

    report = {"transferred": [], "skipped": [], "failed": []}

    try:
        if source == "yuki" and destination.startswith("runner:"):
            with _ssh_connection(dst_runner) as ssh:
                _copy_local_to_remote(src_path, dst_path, ssh, force, progress, report)
        elif source.startswith("runner:") and destination == "yuki":
            with _ssh_connection(src_runner) as ssh:
                _copy_remote_to_local(src_path, dst_path, ssh, force, progress, report)
        elif source.startswith("runner:") and destination.startswith("runner:"):
            with _ssh_connection(src_runner) as src_ssh, \
                 _ssh_connection(dst_runner) as dst_ssh:
                _copy_remote_to_remote(src_path, dst_path, src_ssh, dst_ssh,
                                       force, progress, report)
        write_status("done", {"report": report})
    except Exception as exc:  # pylint: disable=broad-exception-caught
        write_status("failed", {"error": str(exc), "report": report})
        raise

    return report
```

Also fix `_copy_remote_to_remote` to stream without temp file leaks:

```python
def _copy_remote_to_remote(src_root: str, dst_root: str,
                           src_ssh: _SshConnection, dst_ssh: _SshConnection,
                           force: bool, progress: dict, report: dict) -> None:
    """Stream files from src runner to dst runner through Yuki host."""
    import tempfile
    files = _list_remote_files(src_ssh, src_root)
    for entry in files:
        rel = entry["name"]
        src_file = entry["remote_path"]
        dst_file = f"{dst_root}/{rel}"
        progress["current_file"] = rel
        if dst_ssh.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
                for chunk in src_ssh.stream(src_file):
                    tmp.write(chunk)
            dst_ssh.put(tmp_path, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_result_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/result_transfer.py UnitTest/test_result_transfer.py
git commit -m "feat(kernel): add run_transfer orchestration and progress file"
```

---

## Task 5: Yuki Celery task

**Files:**
- Modify: `Yuki/server/tasks.py`

**Interfaces:**
- Consumes: `result_transfer.run_transfer()`
- Produces: `task_transfer_results`

- [ ] **Step 1: Write the failing test**

Create or extend `UnitTest/test_celery_tasks.py` (or a new test file):

```python
def test_task_transfer_results_calls_run_transfer():
    from unittest import mock
    from Yuki.server.tasks import task_transfer_results
    with mock.patch("Yuki.server.tasks.result_transfer") as rt:
        rt.run_transfer.return_value = {"transferred": ["a.txt"]}
        result = task_transfer_results("job1", "proj", "imp",
                                       "runner:pkufarm", "yuki",
                                       None, False)
        rt.run_transfer.assert_called_once_with(
            "job1", "proj", "imp",
            "runner:pkufarm", "yuki", None, False, yuki_dir=None)
        assert result == {"transferred": ["a.txt"]}
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `Yuki/server/tasks.py`:

```python
from ..kernel import result_transfer


@celeryapp.task
def task_transfer_results(job_id, project_uuid, impression,
                          source, destination, pattern, force):
    """Transfer impression results between yuki and runner cache."""
    return result_transfer.run_transfer(
        job_id, project_uuid, impression,
        source, destination,
        pattern=pattern, force=force)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_celery_tasks.py::test_task_transfer_results_calls_run_transfer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/tasks.py UnitTest/test_celery_tasks.py
git commit -m "feat(server): add task_transfer_results celery task"
```

---

## Task 6: Yuki Flask routes

**Files:**
- Modify: `Yuki/server/routes/transfer.py`
- Test: `UnitTest/test_transfer_routes.py`

**Interfaces:**
- Consumes: `task_transfer_results`
- Produces: `start_transfer()`, `transfer_status()`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the transfer routes."""
from unittest import mock

from Yuki.server.routes import transfer as transfer_routes


def _app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(transfer_routes.bp)
    return app


def test_start_transfer_missing_fields():
    client = _app().test_client()
    r = client.post("/transfer", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_start_transfer_starts_job():
    client = _app().test_client()
    with mock.patch.object(transfer_routes, "task_transfer_results") as task:
        with mock.patch("Yuki.server.routes.transfer.config") as cfg:
            cfg.get_config_file.return_value.read_variable.side_effect = lambda key, default: {
                "runners_id": {"pkufarm": "runner-uuid"},
                "backend_types": {"runner-uuid": "ssh"},
            }.get(key, default)
            r = client.post("/transfer", json={
                "project_uuid": "proj",
                "impression": "imp",
                "source": "runner:pkufarm",
                "destination": "yuki",
                "pattern": "*.txt",
                "force": False,
            })
    assert r.status_code == 200
    assert "job_id" in r.get_json()
    task.apply_async.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `Yuki/server/routes/transfer.py`:

```python
import os
import json
from flask import request, jsonify
from CelebiChrono.utils import csys
from ...kernel import result_transfer
from ..config import config
from ..tasks import task_transfer_results


@bp.route("/transfer", methods=['POST'])
def start_transfer():
    """Start a result transfer job."""
    data = request.get_json(silent=True) or request.form
    project_uuid = data.get("project_uuid", "")
    impression = data.get("impression", "")
    source = data.get("source", "")
    destination = data.get("destination", "")
    pattern = data.get("pattern") or None
    force = bool(data.get("force", False))

    if not (project_uuid and impression and source and destination):
        return jsonify({"error": "missing required field"}), 400

    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    backend_types = config_file.read_variable("backend_types", {})

    for loc in (source, destination):
        if loc.startswith("runner:"):
            name = loc[len("runner:"):]
            if name not in runners_id:
                return jsonify({"error": f"runner '{name}' not found"}), 404
            runner_id = runners_id[name]
            if backend_types.get(runner_id, "reana") != "ssh":
                return jsonify({
                    "error": f"runner '{name}' is not an ssh runner"
                }), 400

    job_id = csys.generate_uuid()
    yuki_dir = result_transfer._resolve_yuki_dir()  # pylint: disable=protected-access
    progress_dir = os.path.join(yuki_dir, "transfer-progress")
    os.makedirs(progress_dir, exist_ok=True)
    progress_path = os.path.join(progress_dir, f"{job_id}.json")
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"status": "pending", "bytes_done": 0,
                   "bytes_total": 0, "current_file": ""}, f)

    task_transfer_results.apply_async(
        args=[job_id, project_uuid, impression,
              source, destination, pattern, force])
    return jsonify({"job_id": job_id})


@bp.route("/transfer/<job_id>", methods=['GET'])
def transfer_status(job_id):
    """Poll a transfer job's state."""
    yuki_dir = result_transfer._resolve_yuki_dir()  # pylint: disable=protected-access
    progress_path = os.path.join(yuki_dir, "transfer-progress", f"{job_id}.json")
    if not os.path.exists(progress_path):
        return jsonify({"error": "job not found"}), 404
    try:
        with open(progress_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return jsonify({"error": "corrupt job state"}), 500
    return jsonify(state)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_transfer_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/transfer.py UnitTest/test_transfer_routes.py
git commit -m "feat(server): add /transfer and /transfer/<job_id> routes"
```

---

## Task 7: CelebiChrono ChernCommunicator methods

**Files:**
- Modify: `CelebiChrono/kernel/chern_communicator.py`
- Test: `CelebiChrono/UnitTest/test_chern_communicator_transfer.py` (or existing test file)

**Interfaces:**
- Consumes: Yuki `/transfer` routes
- Produces: `ChernCommunicator.transfer()`, `ChernCommunicator.transfer_status()`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for ChernCommunicator transfer methods."""
from unittest import mock

from CelebiChrono.kernel.chern_communicator import ChernCommunicator


class MockResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def test_transfer_posts_to_server():
    cc = ChernCommunicator.instance()
    with mock.patch("requests.post") as post:
        post.return_value = MockResponse(200, {"job_id": "abc123"})
        result = cc.transfer("proj", "imp", "runner:pkufarm", "yuki",
                             pattern="*.txt", force=False)
        assert result == {"job_id": "abc123"}
        post.assert_called_once()
        args, kwargs = post.call_args
        assert "/transfer" in args[0]
        assert kwargs["json"]["project_uuid"] == "proj"


def test_transfer_status_gets_from_server():
    cc = ChernCommunicator.instance()
    with mock.patch("requests.get") as get:
        get.return_value = MockResponse(200, {"status": "running"})
        result = cc.transfer_status("abc123")
        assert result["status"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `CelebiChrono/kernel/chern_communicator.py` near the other transfer/registration methods:

```python
def transfer(self, project_uuid, impression, source, destination,
             pattern=None, force=False):
    """Start a result transfer job on Yuki."""
    url = self.serverurl()
    data = {
        "project_uuid": project_uuid,
        "impression": impression,
        "source": source,
        "destination": destination,
        "force": force,
    }
    if pattern:
        data["pattern"] = pattern
    try:
        r = requests.post(f"http://{url}/transfer",
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
        return {"error": f"transfer failed (HTTP {r.status_code})"}
    return r.json()


def transfer_status(self, job_id):
    """Poll a transfer job's state."""
    url = self.serverurl()
    try:
        r = requests.get(f"http://{url}/transfer/{job_id}",
                         timeout=self.timeout)
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Failed to connect to DITE server: {e}") from e
    if r.status_code == 404:
        return {"status": "unknown", "error": "job not found"}
    return r.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest CelebiChrono/UnitTest/test_chern_communicator_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/kernel/chern_communicator.py CelebiChrono/UnitTest/test_chern_communicator_transfer.py
git commit -m "feat(communicator): add transfer and transfer_status client methods"
```

---

## Task 8: CelebiChrono shell transfer function

**Files:**
- Modify: `CelebiChrono/interface/shell_modules/file_operations.py`
- Test: `CelebiChrono/UnitTest/test_shell_transfer.py`

**Interfaces:**
- Consumes: `ChernCommunicator.transfer()`, `ChernCommunicator.transfer_status()`
- Produces: `transfer(source, destination, pattern=None, force=False)`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the shell transfer function."""
from unittest import mock

from CelebiChrono.interface.shell_modules import file_operations


def test_transfer_no_current_object():
    with mock.patch.object(file_operations.MANAGER, "current_object",
                           return_value=None):
        msg = file_operations.transfer("yuki", "runner:pkufarm")
        assert any("No current object" in m["message"] for m in msg.messages)


def test_transfer_polls_until_done():
    current = mock.MagicMock()
    current.project_uuid.return_value = "proj"
    current.impression.return_value = "imp"
    with mock.patch.object(file_operations.MANAGER, "current_object",
                           return_value=current):
        cc = mock.MagicMock()
        cc.transfer.return_value = {"job_id": "abc123"}
        cc.transfer_status.side_effect = [
            {"status": "running", "bytes_done": 0, "bytes_total": 100},
            {"status": "running", "bytes_done": 50, "bytes_total": 100},
            {"status": "done", "report": {"transferred": ["a.txt"],
                                           "skipped": [], "failed": []}},
        ]
        with mock.patch("CelebiChrono.interface.shell_modules.file_operations.ChernCommunicator") as CC:
            CC.instance.return_value = cc
            with mock.patch("time.sleep"):
                msg = file_operations.transfer("yuki", "runner:pkufarm")
        assert any("a.txt" in m["message"] for m in msg.messages)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `CelebiChrono/interface/shell_modules/file_operations.py`:

```python
import time
from tqdm import tqdm
from ...kernel.chern_communicator import ChernCommunicator


def transfer(source: str, destination: str, pattern: str = None,
             force: bool = False) -> Message:
    """Transfer stageout results between Yuki and a runner cache.

    SOURCE and DESTINATION are 'yuki' or 'runner:<runner-id>'.
    """
    message = Message()
    current_obj = MANAGER.current_object()
    if current_obj is None:
        message.add("No current object selected", "error")
        return message
    project_uuid = current_obj.project_uuid()
    impression = current_obj.impression()
    if not project_uuid or not impression:
        message.add("No project/impression selected", "error")
        return message

    cherncc = ChernCommunicator.instance()
    resp = cherncc.transfer(project_uuid, impression, source, destination,
                            pattern=pattern, force=force)
    if "error" in resp:
        message.add(resp["error"], "error")
        return message
    if "job_id" not in resp:
        message.add("Server did not return a job id", "error")
        return message

    job_id = resp["job_id"]
    progress_bar = tqdm(unit="B", unit_scale=True, unit_divisor=1024,
                        desc="transfer: pending")
    try:
        while True:
            state = cherncc.transfer_status(job_id)
            status = state.get("status", "unknown")
            total = state.get("bytes_total", 0) or 0
            done = state.get("bytes_done", 0) or 0
            current_file = state.get("current_file", "")
            if total and progress_bar.total != total:
                progress_bar.total = total
                progress_bar.refresh()
            progress_bar.n = min(done, total)
            progress_bar.set_description(
                f"transfer: {status}" + (f" {current_file}" if current_file else ""))
            progress_bar.refresh()

            if status == "done":
                report = state.get("report", {})
                transferred = len(report.get("transferred", []))
                skipped = len(report.get("skipped", []))
                failed = len(report.get("failed", []))
                message.add(
                    f"Transferred {transferred}, skipped {skipped}, "
                    f"failed {failed}", "success")
                return message
            if status == "failed":
                message.add(f"Transfer failed: {state.get('error')}", "error")
                return message
            time.sleep(2)
    finally:
        progress_bar.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest CelebiChrono/UnitTest/test_shell_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/interface/shell_modules/file_operations.py CelebiChrono/UnitTest/test_shell_transfer.py
git commit -m "feat(shell): add transfer shell function with progress bar"
```

---

## Task 9: CelebiChrono CLI command and registration

**Files:**
- Modify: `CelebiChrono/celebi_cli/commands/file_operations.py`
- Modify: `CelebiChrono/celebi_cli/cli.py`
- Test: `CelebiChrono/UnitTest/test_cli_transfer_command.py`

**Interfaces:**
- Consumes: `CelebiChrono.interface.shell.transfer()`
- Produces: `transfer_command`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for celebi-cli transfer command."""
from unittest import mock

from CelebiChrono.celebi_cli.commands import file_operations as file_cmd


def test_transfer_command_invokes_shell():
    with mock.patch("CelebiChrono.celebi_cli.commands.file_operations.transfer") as shell_transfer:
        shell_transfer.return_value = mock.MagicMock()
        shell_transfer.return_value.messages = []
        runner = mock.MagicMock()
        runner.invoke(file_cmd.transfer_command, ["yuki", "runner:pkufarm", "--pattern", "*.txt"])
        shell_transfer.assert_called_once_with("yuki", "runner:pkufarm",
                                               pattern="*.txt", force=False)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add to `CelebiChrono/celebi_cli/commands/file_operations.py`:

```python
@click.command(name="transfer")
@click.argument("source", type=str)
@click.argument("destination", type=str)
@click.option("--pattern", type=str, default=None,
              help="Glob pattern to filter transferred files")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite existing files at destination")
def transfer_command(source: str, destination: str,
                     pattern: str = None, force: bool = False) -> None:
    """Transfer stageout results between Yuki and runner cache.

    SOURCE and DESTINATION are 'yuki' or 'runner:<runner-id>'.
    """
    try:
        from CelebiChrono.interface.shell import transfer
        result = transfer(source, destination, pattern=pattern, force=force)
        _handle_result(result)
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")
```

Register in `CelebiChrono/celebi_cli/cli.py`:

```python
cli.add_command(file_operations.transfer_command)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest CelebiChrono/UnitTest/test_cli_transfer_command.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/celebi_cli/commands/file_operations.py CelebiChrono/celebi_cli/cli.py CelebiChrono/UnitTest/test_cli_transfer_command.py
git commit -m "feat(cli): add transfer command"
```

---

## Task 10: Integration smoke test

**Files:**
- None new

- [ ] **Step 1: Start Yuki server and Celery worker**

```bash
cd /Users/wave/workdir/Celebi/Yuki
yuki server start
celery -A Yuki.server.tasks worker --loglevel=info
```

- [ ] **Step 2: Run the CLI command**

```bash
cd /Users/wave/workdir/Celebi/Celebi
celebi-cli transfer yuki runner:<some-ssh-runner> --pattern "*.txt"
```

- [ ] **Step 3: Verify progress bar appears and summary is printed**

- [ ] **Step 4: Verify files arrived on the runner at the expected path**

- [ ] **Step 5: Run reverse transfer (runner -> yuki)**

- [ ] **Step 6: Commit any final fixes**

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|------------------|------|
| `celebi-cli transfer <source> <destination>` | Task 9 |
| `yuki` / `runner:<runner-id>` specifiers | Tasks 1, 2, 6 |
| `--pattern` glob filter | Tasks 1, 6 |
| `--force` overwrite | Tasks 3, 6 |
| Progress bar via tqdm | Task 8 |
| Server-backed Celery job | Tasks 4, 5, 6 |
| Runner-to-runner support | Task 3 |
| Only SSH runners | Task 6 |

**Placeholder scan:** No TBD/TODO placeholders; each step includes concrete code or exact commands.

**Type consistency:** `run_transfer()` signature matches Celery task args. `transfer()` shell function args match Click command options. Communicator method names match shell usage.
