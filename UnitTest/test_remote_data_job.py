"""Tests for the register-remote-data celery job."""
import json
import os
from unittest import mock

import pytest

from CelebiChrono.utils.file_utils import dir_md5
from CelebiChrono.utils.metadata import ConfigFile
from Yuki.kernel import remote_data_ops
import importlib
config_module = importlib.import_module("Yuki.server.config")


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

    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        result = remote_data_ops.register_remote_data_job(
            "r1", str(data), "proj", "mydata", updates.append)

    assert result["uuid"] == md5
    assert result["descriptor"] == "mydata"

    # stage transitions
    statuses = [u["status"] for u in updates]
    assert statuses[0] == "hashing"
    assert "copying" in statuses
    assert "registering" not in statuses

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
    assert status["status"] == "archived"

    # while the copy was in flight, the impression was "running"
    running_updates = [u for u in updates if u["status"] == "copying"]
    assert running_updates, "expected a copying stage update"


def test_register_remote_data_job_copy_failure_marks_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    data = _fixture_data(tmp_path)
    md5 = dir_md5(str(data))
    fake = FakeSsh(md5)

    def failing_exec(command, timeout=None):
        fake.commands.append(command)
        if command.startswith("python3 -c"):
            return md5, "", 0
        return "", "disk full", 1

    fake.exec = failing_exec
    updates = []
    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        with pytest.raises(RuntimeError) as exc:
            remote_data_ops.register_remote_data_job(
                "r1", str(data), "proj", "d", updates.append)
    assert "copy failed" in str(exc.value)
    # the impression exists with status failed
    result_updates = [u for u in updates if u["status"] == "copying"]
    assert result_updates
    status_file = tmp_path / "Storage" / "proj"
    statuses = list(status_file.glob("*/status.json"))
    assert len(statuses) == 1
    assert json.loads(statuses[0].read_text())["status"] == "failed"


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
    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        with pytest.raises(RuntimeError) as exc:
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
    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        tasks.task_register_remote_data.run(
            "job-1", "r1", str(data), "proj", "d")
    state = remote_data_ops.read_job_state(str(tmp_path), "job-1")
    assert state["status"] == "done"
    assert state["result"]["uuid"] == md5
    assert state["error"] is None


def test_register_remote_data_job_without_project_context(monkeypatch, tmp_path):
    """The job must work when no Celebi project context exists (celery worker).

    VImpression.__init__ on older CelebiChrono releases does
    csys.project_path() + "/.chern/..." and crashes with TypeError when
    project_path() is None. The job must not depend on project context.
    """
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    data = _fixture_data(tmp_path)
    md5 = dir_md5(str(data))
    fake = FakeSsh(md5)
    # Simulate running outside any Celebi project (docker celery worker).
    monkeypatch.setattr("CelebiChrono.utils.csys.project_path",
                        lambda: None)
    updates = []
    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        result = remote_data_ops.register_remote_data_job(
            "r1", str(data), "proj", "mydata", updates.append)
    assert result["uuid"] == md5
    assert (tmp_path / "Storage" / "proj" / result["impression_uuid"] /
            "remote.json").exists()


def test_register_remote_data_job_too_old_celebichrono(monkeypatch, tmp_path):
    """A Celebichrono without generate_imp_uuid must produce an actionable error."""
    from CelebiChrono.kernel import vimpression
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    data = _fixture_data(tmp_path)
    md5 = dir_md5(str(data))
    fake = FakeSsh(md5)
    with mock.patch("Yuki.kernel.ssh_workflow._SshConnection",
                    return_value=fake):
        monkeypatch.delattr(vimpression.VImpression, "generate_imp_uuid")
        with pytest.raises(RuntimeError) as exc:
            remote_data_ops.register_remote_data_job(
                "r1", str(data), "proj", "d", lambda s: None)
    assert "too old" in str(exc.value)
    assert "CELEBI_DIR" in str(exc.value)


class _StubConfig:
    def __init__(self, root):
        self.root = root

    def get_job_path(self, project, impression):
        return str(self.root / "Storage" / project / impression)

    def get_config_file(self):
        return ConfigFile(str(self.root / "config.json"))

    def get_job_config_path(self, project, impression):
        return str(self.root / "Storage" / project / impression / "config.json")


def test_file_status_lists_remote_hosted_files(monkeypatch, tmp_path):
    from Yuki.kernel.impression_storage import ImpressionStorage

    job_dir = tmp_path / "Storage" / "proj" / "imp-1"
    job_dir.mkdir(parents=True)
    marker = ConfigFile(str(job_dir / "remote.json"))
    marker.write_variable("host_runner_id", "r1")
    marker.write_variable("remote_path", "/remote/imp")
    monkeypatch.setattr(config_module, "config", _StubConfig(tmp_path))

    calls = []

    class FakeSsh:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def walk_files(self, path):
            calls.append(path)
            yield "a.txt", "/remote/imp/a.txt", 10
            yield "sub/b.root", "/remote/imp/sub/b.root", 20

    with mock.patch("Yuki.kernel.remote_data_ops._ssh_connection",
                    return_value=FakeSsh()):
        rows = ImpressionStorage("proj", "imp-1").file_status("stageout")
    assert calls == ["/remote/imp"]
    assert [r["name"] for r in rows] == ["a.txt", "sub/b.root"]
    assert all(r["in_runner"] and not r["in_yuki"] for r in rows)
    assert rows[1]["size"] == 20

    # second call served from cache — no ssh round-trip
    with mock.patch("Yuki.kernel.remote_data_ops._ssh_connection") as patched:
        rows2 = ImpressionStorage("proj", "imp-1").file_status("stageout")
        patched.assert_not_called()
    assert rows2 == rows


def test_file_status_no_remote_marker_returns_empty(monkeypatch, tmp_path):
    from Yuki.kernel.impression_storage import ImpressionStorage
    job_dir = tmp_path / "Storage" / "proj" / "imp-1"
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(config_module, "config", _StubConfig(tmp_path))
    rows = ImpressionStorage("proj", "imp-1").file_status("stageout")
    assert rows == []
