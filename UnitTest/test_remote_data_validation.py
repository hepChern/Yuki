"""Tests for submit-time validation of remote-hosted data runner binding."""
import json
import os
from unittest import mock

from Yuki.server import tasks


def _marker(tmp_path, project, impression, host_runner):
    marker_dir = tmp_path / ".Yuki" / "Storage" / project / impression
    marker_dir.mkdir(parents=True)
    with open(marker_dir / "remote.json", "w", encoding="utf-8") as f:
        json.dump({"host_runner_id": host_runner, "source_path": "/src",
                   "remote_path": "/remote/x"}, f)


def _input_job(impression, is_input=True):
    job = mock.MagicMock()
    job.is_input = is_input
    job.job_type.return_value = "analysis"
    job.path = f"/store/{impression}"
    return job


def test_mismatched_runner_marks_workflow_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _marker(tmp_path, "proj", "imp-abc", "runner-A")
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-abc")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    workflow.set_workflow_status.assert_called_once_with("failed")
    workflow.run.assert_not_called()
    args, kwargs = workflow.jobs[0].set_status.call_args
    assert "imp-abc" in args[1]
    assert "collect" in args[1]


def test_matching_runner_proceeds(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _marker(tmp_path, "proj", "imp-abc", "runner-A")
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-abc")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-A")

    workflow.run.assert_called_once()


def test_no_marker_no_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    workflow = mock.MagicMock()
    workflow.jobs = [_input_job("imp-no-marker")]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    workflow.run.assert_called_once()
    workflow.set_workflow_status.assert_not_called()
