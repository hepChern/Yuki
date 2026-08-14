"""Tests for listing conda environments on runners."""
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


CONDA_ENV_LIST = """# conda environments:
#
base                  *  /opt/miniconda3
celebi                   /opt/miniconda3/envs/celebi
                         /home/user/.conda/envs/unnamed
"""


def test_parse_conda_env_list():
    envs = runner_probe.parse_conda_env_list(CONDA_ENV_LIST)
    assert envs == [
        {"name": "base", "path": "/opt/miniconda3", "active": True},
        {"name": "celebi", "path": "/opt/miniconda3/envs/celebi", "active": False},
        {"name": "", "path": "/home/user/.conda/envs/unnamed", "active": False},
    ]


def test_runner_envs_native(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj)
    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: "/usr/bin/conda")
    monkeypatch.setattr(
        runner_probe.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, CONDA_ENV_LIST, ""))

    r = _app(runner_routes.bp).test_client().get("/runner-envs/local")
    assert r.status_code == 200
    body = r.get_json()
    assert body["error"] is None
    assert [e["name"] for e in body["envs"]] == ["base", "celebi", ""]


def test_runner_envs_native_no_conda(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj)
    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: None)
    body = _app(runner_routes.bp).test_client().get("/runner-envs/local").get_json()
    assert body["envs"] == []
    assert "conda not found" in body["error"]


def test_runner_envs_ssh(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj, name="cluster", backend="ssh",
                  settings={"ssh_host": "h", "ssh_user": "u"})
    mock_client = mock.MagicMock()
    mock_client.exec_command.return_value = (
        mock.MagicMock(),
        mock.MagicMock(**{"read.return_value": CONDA_ENV_LIST.encode()}),
        mock.MagicMock(**{"read.return_value": b""}),
    )
    with mock.patch("paramiko.SSHClient") as ssh_cls:
        ssh_cls.return_value = mock_client
        body = _app(runner_routes.bp).test_client().get("/runner-envs/cluster").get_json()
    assert body["error"] is None
    assert len(body["envs"]) == 3
    assert body["envs"][0]["active"] is True


def test_runner_envs_ssh_connect_failure(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj, name="cluster", backend="ssh",
                  settings={"ssh_host": "h", "ssh_user": "u"})
    with mock.patch("paramiko.SSHClient") as ssh_cls:
        ssh_cls.return_value.connect.side_effect = Exception("no route")
        body = _app(runner_routes.bp).test_client().get("/runner-envs/cluster").get_json()
    assert body["envs"] == []
    assert "no route" in body["error"]


def test_runner_envs_reana_rejected(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj, name="cern", backend="reana")
    body = _app(runner_routes.bp).test_client().get("/runner-envs/cern").get_json()
    assert body["envs"] == []
    assert "reana" in body["error"]


def test_runner_envs_unknown_404(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().get("/runner-envs/ghost")
    assert r.status_code == 404
    assert "error" in r.get_json()
