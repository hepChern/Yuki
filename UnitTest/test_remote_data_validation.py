"""Tests for submit-time validation of remote-hosted data runner binding."""
import json
import os
from unittest import mock

from Yuki.server import tasks


def _marker(tmp_path, project, impression, host_runner):
    marker_dir = tmp_path / ".Yuki" / "Storage" / project / impression
    marker_dir.mkdir(parents=True, exist_ok=True)
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
    workflow.jobs = [_input_job("imp-abc"), _input_job("imp-x", is_input=False)]
    with mock.patch.object(tasks, "VJob"), \
            mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    workflow.set_workflow_status.assert_called_once_with("failed")
    workflow.run.assert_not_called()
    # the workflow's own execution job carries the dissonance...
    args, kwargs = workflow.jobs[1].set_status.call_args
    assert "imp-abc" in args[1]
    assert "collect" in args[1]
    # ...while the shared input impression stays untouched
    workflow.jobs[0].set_status.assert_not_called()


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


def _write_impression(tmp_path, project, impression, object_type, deps,
                      environment=None):
    imp_dir = tmp_path / ".Yuki" / "Storage" / project / impression
    (imp_dir / "contents").mkdir(parents=True)
    imp_dir.joinpath("config.json").write_text(json.dumps({
        "uuid": impression,
        "object_type": object_type,
        "dependencies": deps,
    }), encoding="utf-8")
    yaml_lines = [f"uuid: {impression}"]
    if environment:
        yaml_lines.append(f"environment: {environment}")
    imp_dir.joinpath("contents", "celebi.yaml").write_text(
        "\n".join(yaml_lines) + "\n", encoding="utf-8")


def _status(tmp_path, project, impression):
    status_file = (tmp_path / ".Yuki" / "Storage" / project / impression
                   / "status.json")
    if not status_file.exists():
        return None
    return json.loads(status_file.read_text(encoding="utf-8"))["status"]


def test_integration_mismatched_runner_marks_real_workflow_failed(
        monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".Yuki" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"backend_types": {"runner-B": "native"}}),
                           encoding="utf-8")
    _write_impression(tmp_path, "proj", "imp-x", "task", ["imp-abc"],
                      environment="analysis")
    _write_impression(tmp_path, "proj", "imp-abc", "task", [],
                      environment="rawdata")
    _marker(tmp_path, "proj", "imp-abc", "runner-A")

    tasks.task_exec_impression.run("proj", "imp-x", "runner-B")

    # the workflow's own execution job is marked dissonant...
    assert _status(tmp_path, "proj", "imp-x") == "dissonance"
    # ...the shared input impression is left untouched...
    assert _status(tmp_path, "proj", "imp-abc") is None
    # ...and the workflow is failed before run() ever executed
    results = list((tmp_path / ".Yuki" / "Workflows" / "proj")
                   .glob("*/results.json"))
    assert len(results) == 1
    assert json.loads(results[0].read_text(encoding="utf-8"))["results"]["status"] \
        == "failed"
