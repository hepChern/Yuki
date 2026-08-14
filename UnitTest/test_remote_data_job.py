"""Tests for the register-remote-data celery job."""
import json
import os
from unittest import mock

import pytest

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
