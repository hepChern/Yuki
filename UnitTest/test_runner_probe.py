"""Tests for runner capability probing and test-runner endpoints."""
import json
import os
import subprocess
import tempfile
from unittest import mock

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.server import runner_probe
from Yuki.server.routes import runner as runner_routes


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _temp_config(monkeypatch):
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, ".Yuki"), exist_ok=True)
    config_obj = mock.MagicMock()
    config_obj.config_path = os.path.join(tmp, ".Yuki", "config.json")
    config_obj.get_config_file.return_value = ConfigFile(config_obj.config_path)
    monkeypatch.setattr(runner_routes, "config", config_obj)
    return config_obj


def _write_runner(config_obj, name="local", backend="native", settings=None):
    runner_id = "r-uuid"
    data = {
        "runners": [name],
        "runners_id": {name: runner_id},
        "urls": {runner_id: ""},
        "tokens": {runner_id: ""},
        "backend_types": {runner_id: backend},
    }
    if settings:
        data["runner_settings"] = {runner_id: settings}
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return runner_id


def test_probe_native_all_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_probe.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_probe.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "8.1.0\n", ""))
    checks = runner_probe.probe_native({"workdir": str(tmp_path)})
    assert checks["snakemake"]["ok"] is True
    assert checks["snakemake"]["version"] == "8.1.0"
    assert checks["conda"]["ok"] is True
    assert checks["workdir_writable"]["ok"] is True


def test_probe_native_missing_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: None)
    checks = runner_probe.probe_native({"workdir": str(tmp_path)})
    assert checks["snakemake"]["ok"] is False
    assert "not found" in checks["snakemake"]["error"]
    assert checks["conda"]["ok"] is False
    assert checks["workdir_writable"]["ok"] is True


def test_test_runner_native_persists_health(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch)
    runner_id = _write_runner(config_obj, settings={"workdir": str(tmp_path)})
    monkeypatch.setattr(runner_probe.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_probe.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "1.0\n", ""))

    r = _app(runner_routes.bp).test_client().get("/test-runner/local")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert "checked_at" in body

    cfg = json.load(open(config_obj.config_path, encoding="utf-8"))
    assert cfg["runner_health"][runner_id]["status"] == "ok"


def test_test_runner_ssh_failure_marks_failed(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj, name="cluster", backend="ssh",
                  settings={"ssh_host": "h", "ssh_user": "u"})
    with mock.patch("paramiko.SSHClient") as ssh_cls:
        ssh_cls.return_value.connect.side_effect = Exception("no route")
        r = _app(runner_routes.bp).test_client().get("/test-runner/cluster")
    body = r.get_json()
    assert body["status"] == "failed"
    assert body["checks"]["connectivity"]["ok"] is False


def test_test_runner_unknown_404(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().get("/test-runner/ghost")
    assert r.status_code == 404


def test_runner_health_untested_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj)
    client = _app(runner_routes.bp).test_client()
    assert client.get("/runner-health/local").get_json() == {"status": "untested"}

    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: None)
    client.get("/test-runner/local")
    assert client.get("/runner-health/local").get_json()["status"] == "failed"
