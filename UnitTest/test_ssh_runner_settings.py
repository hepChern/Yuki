"""Tests for ssh runner settings consumption."""
import json
import os
from unittest import mock

from Yuki.kernel.ssh_workflow import SshWorkflow


def _workflow(tmp_path, monkeypatch, config_data):
    yuki_dir = tmp_path / ".Yuki"
    yuki_dir.mkdir(parents=True)
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
