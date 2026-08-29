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
