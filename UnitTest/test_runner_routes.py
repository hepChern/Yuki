"""Tests for runner management routes with SSH backend."""
import json
import os
import tempfile
from unittest import mock

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.server.routes import runner as runner_routes


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _temp_config(monkeypatch):
    """Return a YukiConfig-like object pointing at a temp HOME."""
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, ".Yuki"), exist_ok=True)
    config_obj = mock.MagicMock()
    config_obj.home_dir = tmp
    config_obj.config_path = os.path.join(tmp, ".Yuki", "config.json")
    config_obj.storage_path = os.path.join(tmp, ".Yuki", "Storage")
    config_obj.get_config_file.return_value = ConfigFile(config_obj.config_path)
    monkeypatch.setattr(runner_routes, "config", config_obj)
    return tmp, config_obj


def test_register_runner_stores_ssh_config(monkeypatch):
    _temp_config(monkeypatch)

    app = _app(runner_routes.bp)
    c = app.test_client()

    r = c.post("/register-runner", data={
        "runner": "mycluster",
        "url": "",
        "token": "",
        "backend_type": "ssh",
        "ssh_host": "cluster.example.com",
        "ssh_user": "alice",
        "ssh_key_path": "~/.ssh/id_rsa",
        "ssh_port": "2222",
        "remote_workdir": "/data/yuki",
    })
    assert r.status_code == 200
    assert b"successful" in r.data

    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["mycluster"]
    assert cfg["backend_types"][runner_id] == "ssh"
    assert cfg["ssh_hosts"][runner_id] == "cluster.example.com"
    assert cfg["ssh_users"][runner_id] == "alice"
    assert cfg["ssh_key_paths"][runner_id] == "~/.ssh/id_rsa"
    assert cfg["ssh_ports"][runner_id] == 2222
    assert cfg["remote_workdirs"][runner_id] == "/data/yuki"


def test_runners_config_includes_ssh_fields(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["mycluster"],
            "runners_id": {"mycluster": runner_id},
            "urls": {runner_id: ""},
            "tokens": {runner_id: ""},
            "backend_types": {runner_id: "ssh"},
            "ssh_hosts": {runner_id: "cluster.example.com"},
            "ssh_users": {runner_id: "alice"},
            "ssh_key_paths": {runner_id: "~/.ssh/id_rsa"},
            "ssh_ports": {runner_id: 22},
            "remote_workdirs": {runner_id: "/tmp/yuki-workflows"},
        }, f)

    app = _app(runner_routes.bp)
    r = app.test_client().get("/runners-config")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) == 1
    assert data[0]["backend_type"] == "ssh"
    assert data[0]["ssh_host"] == "cluster.example.com"
    assert data[0]["ssh_user"] == "alice"


def test_update_runner_switches_to_ssh_and_stores_fields(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["mycluster"],
            "runners_id": {"mycluster": runner_id},
            "urls": {runner_id: "https://reana.example.com"},
            "tokens": {runner_id: "token"},
            "backend_types": {runner_id: "reana"},
        }, f)

    app = _app(runner_routes.bp)
    c = app.test_client()
    r = c.patch("/update-runner/mycluster", json={
        "backend_type": "ssh",
        "ssh_host": "cluster.example.com",
        "ssh_user": "alice",
        "ssh_key_path": "~/.ssh/id_rsa",
        "remote_workdir": "/data/yuki",
    })
    assert r.status_code == 200

    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert cfg["backend_types"][runner_id] == "ssh"
    assert cfg["ssh_hosts"][runner_id] == "cluster.example.com"
    assert cfg["remote_workdirs"][runner_id] == "/data/yuki"


def test_remove_runner_cleans_ssh_config(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["mycluster"],
            "runners_id": {"mycluster": runner_id},
            "urls": {runner_id: ""},
            "tokens": {runner_id: ""},
            "backend_types": {runner_id: "ssh"},
            "ssh_hosts": {runner_id: "cluster.example.com"},
            "ssh_users": {runner_id: "alice"},
            "ssh_key_paths": {runner_id: "~/.ssh/id_rsa"},
            "ssh_ports": {runner_id: 22},
            "remote_workdirs": {runner_id: "/tmp/yuki-workflows"},
        }, f)

    app = _app(runner_routes.bp)
    r = app.test_client().get("/remove-runner/mycluster")
    assert r.status_code == 200

    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert "mycluster" not in cfg["runners"]
    assert runner_id not in cfg.get("ssh_hosts", {})
    assert runner_id not in cfg.get("ssh_users", {})


def test_runner_connection_ssh_uses_paramiko_ping(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["mycluster"],
            "runners_id": {"mycluster": runner_id},
            "urls": {runner_id: ""},
            "tokens": {runner_id: ""},
            "backend_types": {runner_id: "ssh"},
            "ssh_hosts": {runner_id: "cluster.example.com"},
            "ssh_users": {runner_id: "alice"},
            "ssh_key_paths": {runner_id: "~/.ssh/id_rsa"},
            "ssh_ports": {runner_id: 22},
        }, f)

    mock_client = mock.MagicMock()
    with mock.patch("paramiko.SSHClient") as ssh_cls:
        ssh_cls.return_value = mock_client
        mock_client.exec_command.return_value = (
            mock.MagicMock(),
            mock.MagicMock(**{"read.return_value": b"ok", "channel.recv_exit_status.return_value": 0}),
            mock.MagicMock(**{"read.return_value": b""}),
        )

        app = _app(runner_routes.bp)
        r = app.test_client().get("/runner-connection/mycluster")
        assert r.status_code == 200
        assert r.get_json()["status"] == "Connected"
