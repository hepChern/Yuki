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


def test_register_runner_stores_native_settings(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    r = c.post("/register-runner", data={
        "runner": "local", "url": "", "token": "", "backend_type": "native",
        "workdir": "/data/yuki", "cores": "8", "mem_mb": "4096",
        "conda_path": "/opt/conda/bin/conda",
    })
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["local"]
    assert cfg["runner_settings"][runner_id] == {
        "workdir": "/data/yuki", "cores": 8, "mem_mb": 4096,
        "conda_path": "/opt/conda/bin/conda",
    }


def test_register_runner_duplicate_name_409(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    c.post("/register-runner", data={"runner": "local", "url": "",
                                     "token": "", "backend_type": "native"})
    r = c.post("/register-runner", data={"runner": "local", "url": "",
                                         "token": "", "backend_type": "native"})
    assert r.status_code == 409
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert cfg["runners"].count("local") == 1


def test_register_runner_missing_field_400(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().post(
        "/register-runner", data={"runner": "local"})
    assert r.status_code == 400


def test_register_runner_ssh_double_writes_settings(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    c.post("/register-runner", data={
        "runner": "cluster", "url": "", "token": "", "backend_type": "ssh",
        "ssh_host": "h", "ssh_user": "u", "ssh_key_path": "/k",
        "ssh_port": "2222", "remote_workdir": "/remote",
    })
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["cluster"]
    # legacy maps still written
    assert cfg["ssh_hosts"][runner_id] == "h"
    # new map also written
    s = cfg["runner_settings"][runner_id]
    assert s["ssh_host"] == "h" and s["ssh_port"] == 2222
    assert s["remote_workdir"] == "/remote"


def test_update_runner_stores_settings(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["local"], "runners_id": {"local": runner_id},
                   "backend_types": {runner_id: "native"}}, f)
    r = _app(runner_routes.bp).test_client().patch(
        "/update-runner/local", json={"cores": 16, "snakemake_path": "/usr/bin/snakemake"})
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert cfg["runner_settings"][runner_id]["cores"] == 16
    assert cfg["runner_settings"][runner_id]["snakemake_path"] == "/usr/bin/snakemake"


def test_machine_id_unknown_404(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().get("/machine-id/ghost")
    assert r.status_code == 404


def test_runners_url_tolerates_missing_entries(monkeypatch):
    _temp_config(monkeypatch)
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["a", "b"], "runners_id": {"a": "id-a"},
                   "urls": {}}, f)  # "b" has no id, "a" has no url
    r = _app(runner_routes.bp).test_client().get("/runners-url")
    assert r.status_code == 200


def test_runners_config_includes_settings_and_health(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["local"], "runners_id": {"local": runner_id},
            "urls": {runner_id: ""}, "tokens": {runner_id: ""},
            "backend_types": {runner_id: "native"},
            "runner_settings": {runner_id: {"cores": 8}},
            "runner_health": {runner_id: {"status": "ok", "checks": {}}},
        }, f)
    data = _app(runner_routes.bp).test_client().get("/runners-config").get_json()
    assert data[0]["settings"] == {"cores": 8}
    assert data[0]["health"]["status"] == "ok"


def test_runners_config_defaults_without_new_maps(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["local"], "runners_id": {"local": runner_id},
                   "urls": {runner_id: ""}, "tokens": {runner_id: ""},
                   "backend_types": {runner_id: "native"}}, f)
    data = _app(runner_routes.bp).test_client().get("/runners-config").get_json()
    assert data[0]["settings"] == {}
    assert data[0]["health"] == {"status": "untested"}


def test_remove_runner_cleans_settings_health_and_stale_ssh(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    # backend flipped reana after ssh: stale ssh_* entries must still go
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["cluster"], "runners_id": {"cluster": runner_id},
            "urls": {runner_id: ""}, "tokens": {runner_id: ""},
            "backend_types": {runner_id: "reana"},
            "ssh_hosts": {runner_id: "h"},
            "runner_settings": {runner_id: {"cores": 8}},
            "runner_health": {runner_id: {"status": "ok"}},
        }, f)
    r = _app(runner_routes.bp).test_client().get("/remove-runner/cluster")
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert runner_id not in cfg.get("ssh_hosts", {})
    assert runner_id not in cfg.get("runner_settings", {})
    assert runner_id not in cfg.get("runner_health", {})


def test_update_runner_partial_ssh_preserves_existing_fields(monkeypatch):
    """PATCH with only some ssh fields must not wipe the others."""
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    c.post("/register-runner", data={
        "runner": "cluster", "url": "", "token": "", "backend_type": "ssh",
        "ssh_host": "h", "ssh_user": "u", "ssh_key_path": "/k",
        "ssh_port": "2222", "remote_workdir": "/remote",
    })
    r = c.patch("/update-runner/cluster", json={"ssh_key_path": "/newkey"})
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["cluster"]
    assert cfg["ssh_key_paths"][runner_id] == "/newkey"   # updated
    assert cfg["ssh_hosts"][runner_id] == "h"             # preserved
    assert cfg["ssh_users"][runner_id] == "u"             # preserved
    assert cfg["ssh_ports"][runner_id] == 2222            # preserved
    assert cfg["remote_workdirs"][runner_id] == "/remote"  # preserved
