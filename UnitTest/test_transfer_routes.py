"""Tests for the transfer routes."""
import json
import os
import tempfile
from unittest import mock

from Yuki.server.routes import transfer as transfer_routes


def _app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(transfer_routes.bp)
    return app


def _mock_config(runners_id=None, backend_types=None):
    """Return a config mock with runners_id/backend_types lookup tables."""
    cfg = mock.MagicMock()
    cfg.get_config_file.return_value.read_variable.side_effect = (
        lambda key, default: {
            "runners_id": runners_id or {},
            "backend_types": backend_types or {},
        }.get(key, default)
    )
    return cfg


def test_start_transfer_missing_fields():
    client = _app().test_client()
    r = client.post("/transfer", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_start_transfer_unknown_runner():
    client = _app().test_client()
    with mock.patch("Yuki.server.routes.transfer.config",
                    _mock_config()):
        r = client.post("/transfer", json={
            "project_uuid": "proj",
            "impression": "imp",
            "source": "runner:missing",
            "destination": "yuki",
        })
    assert r.status_code == 404
    assert "not found" in r.get_json()["error"]


def test_start_transfer_non_ssh_runner():
    client = _app().test_client()
    with mock.patch("Yuki.server.routes.transfer.config",
                    _mock_config(
                        runners_id={"reanafarm": "runner-uuid"},
                        backend_types={"runner-uuid": "reana"},
                    )):
        r = client.post("/transfer", json={
            "project_uuid": "proj",
            "impression": "imp",
            "source": "runner:reanafarm",
            "destination": "yuki",
        })
    assert r.status_code == 400
    assert "not an ssh runner" in r.get_json()["error"]


def test_start_transfer_starts_job():
    client = _app().test_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(transfer_routes, "task_transfer_results") as task:
            with mock.patch("Yuki.server.routes.transfer.config",
                            _mock_config(
                                runners_id={"pkufarm": "runner-uuid"},
                                backend_types={"runner-uuid": "ssh"},
                            )):
                with mock.patch.object(
                    transfer_routes.result_transfer,
                    "_resolve_yuki_dir",
                    return_value=tmpdir,
                ):
                    r = client.post("/transfer", json={
                        "project_uuid": "proj",
                        "impression": "imp",
                        "source": "runner:pkufarm",
                        "destination": "yuki",
                        "pattern": "*.txt",
                        "force": False,
                    })
    assert r.status_code == 200
    body = r.get_json()
    assert "job_id" in body
    task.apply_async.assert_called_once()


def test_transfer_status():
    client = _app().test_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        progress_dir = os.path.join(tmpdir, "transfer-progress")
        os.makedirs(progress_dir)
        progress_path = os.path.join(progress_dir, "job-123.json")
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump({"status": "done", "bytes_done": 42,
                       "bytes_total": 42, "current_file": "a.txt"}, f)
        with mock.patch.object(
            transfer_routes.result_transfer,
            "_resolve_yuki_dir",
            return_value=tmpdir,
        ):
            r = client.get("/transfer/job-123")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "done"
    assert body["bytes_done"] == 42


def test_transfer_status_unknown_job():
    client = _app().test_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(
            transfer_routes.result_transfer,
            "_resolve_yuki_dir",
            return_value=tmpdir,
        ):
            r = client.get("/transfer/no-such-job")
    assert r.status_code == 404
    assert "error" in r.get_json()
