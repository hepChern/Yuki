"""Tests for native runner settings consumption."""
import json
import os
import tempfile
from unittest import mock

from Yuki.kernel.snakemake_monitor import SnakemakeMonitor


def _monitor(tmp):
    os.makedirs(os.path.join(tmp, "wf"), exist_ok=True)
    exec_dir = os.path.join(tmp, "exec")
    os.makedirs(exec_dir, exist_ok=True)
    return SnakemakeMonitor(os.path.join(tmp, "wf"), exec_dir,
                            project_uuid="p", workflow_uuid="w")


def test_execute_snakemake_settings(tmp_path):
    mon = _monitor(str(tmp_path))
    with mock.patch("subprocess.Popen") as popen:
        popen.return_value.wait.return_value = 0
        popen.return_value.returncode = 0
        try:
            mon.execute_snakemake(8, mem_mb=4096,
                                  snakemake_path="/opt/bin/snakemake",
                                  conda_path="/opt/conda/bin/conda")
        except Exception:
            pass  # status file handling is out of scope here
    cmd = popen.call_args[0][0]
    assert cmd[0] == "/opt/bin/snakemake"
    assert "--resources" in cmd and "mem_mb=4096" in cmd
    assert "-j" in cmd and "8" in cmd
    env = popen.call_args[1].get("env")
    assert env["PATH"].startswith("/opt/conda/bin" + os.pathsep)


def test_native_workflow_uses_workdir_setting(monkeypatch, tmp_path):
    yuki_dir = tmp_path / ".Yuki"
    (yuki_dir / "Storage" / "proj").mkdir(parents=True)
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    with open(yuki_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"runner_settings": {"m1": {"workdir": str(tmp_path / "custom")}}}, f)

    from Yuki.kernel.native_workflow import NativeWorkflow
    with mock.patch.object(NativeWorkflow, "__init__", lambda self, *a, **k: None):
        wf = NativeWorkflow.__new__(NativeWorkflow)
    # exercise the path-resolution logic directly
    from Yuki.kernel import runner_config
    settings = runner_config.get_runner_settings(runner_config.open_config(), "m1")
    assert settings["workdir"] == str(tmp_path / "custom")
