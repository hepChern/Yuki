"""Tests for the engine-log route's on-demand fetch."""
import os
import tempfile
from unittest import mock

from Yuki.server.routes import status as status_routes


def _app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(status_routes.bp)
    return app


def test_engine_log_fetch_generates_on_demand(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", tmp)
    workflow_dir = os.path.join(tmp, ".Yuki", "Workflows", "proj", "wf-1")
    os.makedirs(workflow_dir)
    log_path = os.path.join(workflow_dir, "engine_logs.json")
    assert not os.path.exists(log_path)

    workflow = mock.MagicMock()
    workflow.uuid = "wf-1"

    def generate():
        with open(log_path, "w", encoding="utf-8") as f:
            f.write('{"logs": {"backend": "ssh", "workflow_uuid": "wf-1"}}')

    workflow.get_workflow_logs.side_effect = generate
    with mock.patch.object(status_routes, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        r = _app().test_client().get(
            "/engine-log/proj/wf-1?fetch=true")
    assert r.status_code == 200
    assert b'"backend": "ssh"' in r.data
    workflow.get_workflow_logs.assert_called_once()


def test_engine_log_without_fetch_serves_yuki_log(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", tmp)
    workflow_dir = os.path.join(tmp, ".Yuki", "Workflows", "proj", "wf-1")
    os.makedirs(workflow_dir)
    with open(os.path.join(workflow_dir, "workflow.log"), "w",
              encoding="utf-8") as f:
        f.write("[t0] Constructing the workflow")
    with mock.patch.object(status_routes, "VWorkflow") as vwf:
        r = _app().test_client().get("/engine-log/proj/wf-1")
    assert r.status_code == 200
    body = r.get_json()
    assert body["logs"]["workflow_log"] == "[t0] Constructing the workflow"
    assert body["logs"]["workflow_uuid"] == "wf-1"
    vwf.create.assert_not_called()


def test_engine_log_without_fetch_still_404(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", tmp)
    with mock.patch.object(status_routes, "VWorkflow") as vwf:
        r = _app().test_client().get("/engine-log/proj/wf-1")
    assert r.status_code == 404
    vwf.create.assert_not_called()
