"""Tests for stale-workflow workspace purging."""
import json
import os
from unittest import mock

from Yuki.kernel import liveness
from Yuki.kernel import workflow_purge


def _workflow_mirror(tmp_path, project, workflow, machine_id,
                     status="finished"):
    """A Workflows mirror dir with config.json machine_id and results.json."""
    wf_dir = tmp_path / "Workflows" / project / workflow
    wf_dir.mkdir(parents=True)
    with open(wf_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"machine_id": machine_id}, f)
    with open(wf_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump({"results": {"status": status}}, f)
    return wf_dir


def test_purge_stale_workflows_deletes_non_live(monkeypatch, tmp_path):
    """Non-live workflows of the runner are deleted; others are skipped."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    # live impression a -> wf-live; run config per machine.
    run_dir = tmp_path / "Storage" / "proj" / ("a" * 32) / "r1"
    run_dir.mkdir(parents=True)
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"workflow": "wf-live"}, f)
    liveness.save_live_set("proj", ["a" * 32], [])

    _workflow_mirror(tmp_path, "proj", "wf-live", "r1")
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")
    _workflow_mirror(tmp_path, "proj", "wf-other-runner", "r9")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == [
        {"project": "proj", "workflow": "wf-stale"}]
    skipped = {(s["workflow"], s["reason"]) for s in summary["skipped"]}
    assert ("wf-live", "workflow is live") in skipped
    assert ("wf-other-runner", None) not in skipped  # filtered by runner
    fake_workflow.delete_workspace.assert_called_once_with()


def test_purge_stale_workflows_skips_running(monkeypatch, tmp_path):
    """Running workflows are skipped, never deleted."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1", status="running")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "running"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert summary["skipped"][0]["reason"] == "workflow is running"
    fake_workflow.delete_workspace.assert_not_called()


def test_purge_stale_workflows_without_live_set_skips_all(monkeypatch,
                                                          tmp_path):
    """Projects without a synced set are unknown: nothing is purged."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    _workflow_mirror(tmp_path, "proj", "wf-1", "r1")

    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert summary["skipped"][0]["reason"] == \
        "no live set synced for project"
    vwf.create.assert_not_called()


def test_purge_stale_workflows_dry_run(monkeypatch, tmp_path):
    """Dry-run lists what would go and deletes nothing."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1", dry_run=True)

    assert summary["purged"] == [
        {"project": "proj", "workflow": "wf-stale"}]
    assert summary["dry_run"] is True
    fake_workflow.delete_workspace.assert_not_called()


def test_purge_stale_workflows_delete_failure_is_skip(monkeypatch,
                                                      tmp_path):
    """A delete failure becomes a skip entry, not an abort."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    fake_workflow.delete_workspace.side_effect = OSError("ssh down")
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert "delete failed" in summary["skipped"][0]["reason"]


def _purge_app(monkeypatch, config_vars):
    from Yuki.server.routes import workflow as workflow_routes
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(workflow_routes.bp)
    config_obj = mock.MagicMock()
    from CelebiChrono.utils.metadata import ConfigFile
    import tempfile
    tmp = tempfile.mkdtemp()
    config_obj.config_path = os.path.join(tmp, "config.json")
    config_obj.get_config_file.return_value = ConfigFile(
        config_obj.config_path)
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(config_vars, f)
    monkeypatch.setattr(workflow_routes, "config", config_obj)
    return app


def test_purge_runner_workflows_returns_summary(monkeypatch):
    """/purge-runner-workflows delegates to the kernel purge."""
    from Yuki.server.routes import workflow as workflow_routes
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    summary = {"purged": [], "skipped": [], "dry_run": True}
    with mock.patch.object(workflow_routes, "workflow_purge") as purge:
        purge.purge_stale_workflows.return_value = summary
        r = app.test_client().post(
            "/purge-runner-workflows",
            json={"runner": "pkufarm", "dry_run": True})
    assert r.status_code == 200
    assert r.get_json()["dry_run"] is True
    purge.purge_stale_workflows.assert_called_once_with("r1", True)


def test_purge_runner_workflows_unknown_runner_404(monkeypatch):
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    r = app.test_client().post(
        "/purge-runner-workflows", json={"runner": "nope"})
    assert r.status_code == 404


def test_purge_runner_workflows_missing_runner_400(monkeypatch):
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    r = app.test_client().post(
        "/purge-runner-workflows", json={})
    assert r.status_code == 400
