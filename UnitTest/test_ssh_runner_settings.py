"""Tests for ssh runner settings consumption."""
import json
import os
from unittest import mock

import pytest

from Yuki.kernel.ssh_workflow import SshWorkflow


def _workflow(tmp_path, monkeypatch, config_data):
    yuki_dir = tmp_path / ".Yuki"
    yuki_dir.mkdir(parents=True, exist_ok=True)
    (yuki_dir / "config.json").write_text(json.dumps(config_data))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch.object(SshWorkflow, "__init__", lambda self, *a, **k: None):
        wf = SshWorkflow.__new__(SshWorkflow)
    wf.machine_id = "m1"
    return wf


def test_load_ssh_config_merges_new_and_legacy(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, monkeypatch, {
        "ssh_hosts": {"m1": "legacy-host"},
        "runner_settings": {"m1": {"ssh_user": "new-user", "cores": 16}},
    })
    cfg = wf._load_ssh_config()
    assert cfg["host"] == "legacy-host"   # legacy fallback
    assert cfg["user"] == "new-user"      # new map
    assert cfg["cores"] == 16


def test_wrapper_uses_cores_and_paths(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {
            "ssh_host": "h", "ssh_user": "u", "cores": 8,
            "snakemake_path": "/opt/bin/snakemake",
            "conda_path": "/opt/conda/bin/conda",
            "remote_workdir": "/remote",
        }},
    })
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/wf-uuid"
    wf.logger = lambda msg: None

    written = {}

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def put_text(self, text, path): written[path] = text
        def exec(self, cmd): return "", "", 0

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()):
        wf._start_remote_snakemake()

    wrapper = written["/remote/wf-uuid/yuki_run.sh"]
    assert "/opt/bin/snakemake --use-conda --cores 8" in wrapper
    assert "/opt/conda/bin" in wrapper  # PATH injection for conda


def _workflow_for_layout(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {"ssh_host": "h", "ssh_user": "u",
                                   "remote_workdir": "/remote"}},
    })
    wf.project_uuid = "proj-123"
    wf.uuid = "wf-456"
    return wf


def test_remote_exec_path_has_workflows_and_project(tmp_path, monkeypatch):
    """New layout: <remote_workdir>/workflows/<project_uuid>/<uuid>."""
    yuki_dir = tmp_path / ".Yuki"
    yuki_dir.mkdir(parents=True)
    (yuki_dir / "config.json").write_text(json.dumps({
        "runner_settings": {"m1": {"ssh_host": "h", "ssh_user": "u",
                                   "remote_workdir": "/remote"}},
    }))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    monkeypatch.setenv("HOME", str(tmp_path))

    from Yuki.kernel.vworkflow import VWorkflow

    def fake_init(self, project_uuid, jobs, uuid=None):
        self.project_uuid = project_uuid
        self.uuid = uuid
        self.machine_id = "m1"

    with mock.patch.object(VWorkflow, "__init__", fake_init):
        wf = SshWorkflow("proj-123", [], uuid="wf-456")
    assert wf.remote_exec_path == "/remote/workflows/proj-123/wf-456"


def test_create_remote_structure_creates_impressions_dir(tmp_path, monkeypatch):
    """The reserved impressions/<project_uuid> dir is created alongside."""
    wf = _workflow_for_layout(tmp_path, monkeypatch)
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/workflows/proj-123/wf-456"
    wf.remote_impressions_path = "/remote/impressions/proj-123"
    wf.logger = lambda msg: None
    wf.dependencies = {}
    wf.steps = []

    made_dirs = []

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def mkdir_p(self, path): made_dirs.append(path)
        def put_text(self, text, path): pass

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()), \
            mock.patch.object(SshWorkflow, "get_name", return_value="wf"):
        wf._create_remote_structure()

    assert "/remote/workflows/proj-123/wf-456" in made_dirs
    assert "/remote/impressions/proj-123" in made_dirs


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
    wf.logger = lambda msg: None

    fake_job = mock.MagicMock()
    fake_job.files.return_value = []
    fake_job.environment.return_value = "rawdata"
    fake_job.is_input = True
    fake_job.short_uuid.return_value = "abc1234"
    fake_job.path = f"{marker_dir.parent}/imp-abc"  # parent = Storage/proj-123
    wf.jobs = [fake_job]

    commands = []

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def mkdir_p(self, path): commands.append(("mkdir", path))
        def put(self, local_path, remote_path):
            commands.append(("put", f"{local_path} -> {remote_path}"))
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
    puts = [c for kind, c in commands if kind == "put"]
    data_puts = [c for c in puts if "impabc1234/stageout" in c]
    assert not data_puts, \
        f"expected no SFTP put of remote-hosted data, got: {data_puts}"


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
    wf.logger = lambda msg: None
    fake_job = mock.MagicMock()
    fake_job.files.return_value = []
    fake_job.environment.return_value = "rawdata"
    fake_job.is_input = True
    fake_job.short_uuid.return_value = "abc1234"
    fake_job.path = f"{marker_dir.parent}/imp-abc"
    wf.jobs = [fake_job]

    with mock.patch.object(SshWorkflow, "_ssh", return_value=mock.MagicMock()):
        with pytest.raises(RuntimeError) as exc:
            wf._upload_files_remote()
    assert "another runner" in str(exc.value)


def _input_job_stub(marker_dir, env="rawdata"):
    fake_job = mock.MagicMock()
    fake_job.files.return_value = []
    fake_job.environment.return_value = env
    fake_job.is_input = True
    fake_job.machine_id = "m1"
    fake_job.short_uuid.return_value = "abc1234"
    fake_job.path = str(marker_dir.parent / "imp-abc")
    return fake_job


def _wf_with_input(tmp_path, monkeypatch, fake_job):
    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {"ssh_host": "h", "ssh_user": "u",
                                   "remote_workdir": "/remote/work"}},
    })
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/work/workflows/proj-123/wf-456"
    wf.project_uuid = "proj-123"
    wf.machine_id = "m1"
    wf.snakefile_path = "/local/Snakefile"
    wf.jobs = [fake_job]
    wf.logger = lambda msg: None
    return wf


def test_input_cache_hit_skips_sftp(tmp_path, monkeypatch):
    """A non-empty impressions cache stages via remote cp, no SFTP upload."""
    yuki_dir = tmp_path / ".Yuki"
    (yuki_dir / "Storage" / "proj-123").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_job = _input_job_stub(yuki_dir / "Storage" / "proj-123" / "imp-abc")
    wf = _wf_with_input(tmp_path, monkeypatch, fake_job)

    commands = []
    puts = []

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def mkdir_p(self, path): commands.append(("mkdir", path))
        def exec(self, command, timeout=None):
            commands.append(("exec", command))
            if command.startswith("test -d"):
                return "", "", 0  # cache hit
            return "", "", 0
        def put(self, src, dst): puts.append((src, dst))
        def put_text(self, text, path): pass

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()):
        wf._upload_files_remote()

    execs = [c for kind, c in commands if kind == "exec"]
    cp_cmd = [c for c in execs if "cp -a --reflink=auto" in c][0]
    assert "/remote/work/impressions/proj-123/imp-abc/." in cp_cmd
    assert "impabc1234/stageout" in cp_cmd
    # no SFTP put of data files (only the Snakefile put is allowed)
    data_puts = [p for p in puts if "stageout" in p[1]]
    assert data_puts == []


def test_input_cache_miss_writes_through(tmp_path, monkeypatch):
    """On a miss, data is SFTP-uploaded AND cached into impressions."""
    yuki_dir = tmp_path / ".Yuki"
    src_stageout = yuki_dir / "Storage" / "proj-123" / "imp-abc" / "m1" / "stageout"
    src_stageout.mkdir(parents=True)
    with open(src_stageout / "data.txt", "w") as f:
        f.write("payload")
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_job = _input_job_stub(yuki_dir / "Storage" / "proj-123" / "imp-abc",
                               env="analysis")
    wf = _wf_with_input(tmp_path, monkeypatch, fake_job)

    commands = []

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def mkdir_p(self, path): commands.append(("mkdir", path))
        def exec(self, command, timeout=None):
            commands.append(("exec", command))
            if command.startswith("test -d"):
                return "", "", 1  # cache miss
            return "", "", 0
        def put(self, src, dst): commands.append(("put", f"{src}->{dst}"))
        def put_text(self, text, path): pass

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()):
        wf._upload_files_remote()

    execs = [c for kind, c in commands if kind == "exec"]
    puts = [c for kind, c in commands if kind == "put"]
    assert any("data.txt" in p for p in puts), puts
    write_through = [c for c in execs
                     if "mkdir -p /remote/work/impressions/proj-123/imp-abc" in c]
    assert write_through, execs
