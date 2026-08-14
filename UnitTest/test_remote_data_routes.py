"""Tests for register-remote-data routes and job state."""
import json
import os
from unittest import mock

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.kernel import remote_data_ops
from Yuki.server.routes import remote_data as remote_data_routes


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _temp_config(monkeypatch, tmp_path):
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    config_obj = mock.MagicMock()
    config_obj.config_path = str(tmp_path / "config.json")
    config_obj.get_config_file.return_value = ConfigFile(config_obj.config_path)
    monkeypatch.setattr(remote_data_routes, "config", config_obj)
    return config_obj


def _register_runner(config_obj, name="cluster", backend="ssh"):
    runner_id = "r-uuid"
    data = {"runners": [name], "runners_id": {name: runner_id},
            "backend_types": {runner_id: backend}}
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return runner_id


def test_job_state_roundtrip(tmp_path):
    yuki_dir = tmp_path
    assert remote_data_ops.read_job_state(yuki_dir, "j1") is None
    remote_data_ops.write_job_state(yuki_dir, "j1",
                                    {"status": "hashing", "result": None,
                                     "error": None, "runner_id": "r1",
                                     "remote_path": "/p"})
    state = remote_data_ops.read_job_state(yuki_dir, "j1")
    assert state["status"] == "hashing"
    assert state["runner_id"] == "r1"


def test_read_job_state_corrupt(tmp_path):
    jobs_dir = tmp_path / "register-jobs"
    os.makedirs(str(jobs_dir))
    with open(str(jobs_dir / "j1.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert remote_data_ops.read_job_state(str(tmp_path), "j1") is None


def test_read_job_state_valid_non_dict_json_returns_none(tmp_path):
    """Valid JSON that is not an object must not crash callers on .get."""
    jobs_dir = tmp_path / "register-jobs"
    os.makedirs(str(jobs_dir))
    for payload in ("[]", '"just a string"', "42"):
        with open(str(jobs_dir / "j1.json"), "w", encoding="utf-8") as f:
            f.write(payload)
        assert remote_data_ops.read_job_state(str(tmp_path), "j1") is None


def test_find_existing_registration(tmp_path):
    yuki_dir = tmp_path
    imp_dir = yuki_dir / "Storage" / "proj" / "imp-123"
    os.makedirs(imp_dir / "contents")
    with open(imp_dir / "contents" / "celebi.yaml", "w", encoding="utf-8") as f:
        f.write("environment: rawdata\nuuid: abcdef\ndescriptor: d\n")
    marker = ConfigFile(str(imp_dir / "remote.json"))
    marker.write_variable("host_runner_id", "r1")
    marker.write_variable("source_path", "/src/data")
    marker.write_variable("remote_path", "/remote/imp")

    hit = remote_data_ops.find_existing_registration(str(yuki_dir), "r1", "/src/data")
    assert hit == {"result": {"uuid": "abcdef", "impression_uuid": "imp-123"}}
    assert remote_data_ops.find_existing_registration(
        str(yuki_dir), "r1", "/other") is None


def test_find_inflight_job(tmp_path):
    remote_data_ops.write_job_state(
        str(tmp_path), "job-9",
        {"status": "hashing", "result": None, "error": None,
         "runner_id": "r1", "remote_path": "/p"})
    assert remote_data_ops.find_inflight_job(str(tmp_path), "r1", "/p") == "job-9"
    assert remote_data_ops.find_inflight_job(str(tmp_path), "r1", "/x") is None


def test_register_remote_data_starts_job(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj", "descriptor": "mydata"})
    assert r.status_code == 200
    job_id = r.get_json()["job_id"]
    task.apply_async.assert_called_once()
    state = remote_data_ops.read_job_state(str(tmp_path), job_id)
    assert state["status"] == "hashing"
    assert state["remote_path"] == "/src/data"


def test_register_remote_data_unknown_runner(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    r = _app(remote_data_routes.bp).test_client().post(
        "/register-remote-data",
        json={"runner": "ghost", "remote_path": "/p", "project_uuid": "proj"})
    assert r.status_code == 404


def test_register_remote_data_non_ssh_runner(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj, name="local", backend="native")
    r = _app(remote_data_routes.bp).test_client().post(
        "/register-remote-data",
        json={"runner": "local", "remote_path": "/p", "project_uuid": "proj"})
    assert r.status_code == 400
    assert "ssh" in r.get_json()["error"]


def test_register_remote_data_idempotent(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    imp_dir = tmp_path / "Storage" / "proj" / "imp-123"
    os.makedirs(imp_dir / "contents")
    with open(imp_dir / "contents" / "celebi.yaml", "w", encoding="utf-8") as f:
        f.write("environment: rawdata\nuuid: abcdef\ndescriptor: d\n")
    marker = ConfigFile(str(imp_dir / "remote.json"))
    marker.write_variable("host_runner_id", "r-uuid")
    marker.write_variable("source_path", "/src/data")
    marker.write_variable("remote_path", "/remote/imp")
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj"})
    task.apply_async.assert_not_called()
    assert r.get_json()["result"]["uuid"] == "abcdef"


def test_register_remote_data_missing_field(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    r = _app(remote_data_routes.bp).test_client().post(
        "/register-remote-data",
        json={"runner": "cluster", "remote_path": "/p"})
    assert r.status_code == 400
    assert "missing required field" in r.get_json()["error"]


def test_register_remote_data_form_body(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            data={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj", "descriptor": "mydata"})
    assert r.status_code == 200
    assert r.get_json()["job_id"]
    task.apply_async.assert_called_once()


def test_register_remote_data_inflight(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    runner_id = _register_runner(config_obj)
    remote_data_ops.write_job_state(
        str(tmp_path), "job-7",
        {"status": "hashing", "result": None, "error": None,
         "runner_id": runner_id, "remote_path": "/src/data"})
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj"})
    assert r.status_code == 200
    assert r.get_json() == {"job_id": "job-7"}
    task.apply_async.assert_not_called()


def test_register_remote_data_enqueue_failure(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch, tmp_path)
    _register_runner(config_obj)
    with mock.patch.object(remote_data_routes, "task_register_remote_data") as task:
        task.apply_async.side_effect = RuntimeError("broker down")
        r = _app(remote_data_routes.bp).test_client().post(
            "/register-remote-data",
            json={"runner": "cluster", "remote_path": "/src/data",
                  "project_uuid": "proj"})
    assert r.status_code == 500
    job_id = r.get_json()["job_id"]
    state = remote_data_ops.read_job_state(str(tmp_path), job_id)
    assert state["status"] == "failed"
    assert "broker down" in state["error"]


def test_register_remote_data_status(monkeypatch, tmp_path):
    _temp_config(monkeypatch, tmp_path)
    remote_data_ops.write_job_state(
        str(tmp_path), "job-9",
        {"status": "copying", "result": None, "error": None,
         "runner_id": "r1", "remote_path": "/p"})
    r = _app(remote_data_routes.bp).test_client().get(
        "/register-remote-data/job-9")
    assert r.get_json()["status"] == "copying"
    r2 = _app(remote_data_routes.bp).test_client().get(
        "/register-remote-data/ghost")
    assert r2.status_code == 404
