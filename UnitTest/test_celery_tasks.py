"""Tests for Yuki Celery tasks."""
from unittest import mock

import pytest


def test_task_transfer_results_calls_run_transfer():
    """task_transfer_results delegates to result_transfer.run_transfer."""
    from Yuki.server.tasks import task_transfer_results
    with mock.patch("Yuki.server.tasks.result_transfer") as rt:
        rt.run_transfer.return_value = {"transferred": ["a.txt"]}
        result = task_transfer_results("job1", "proj", "imp",
                                       "runner:pkufarm", "yuki",
                                       None, False)
        rt.run_transfer.assert_called_once_with(
            "job1", "proj", "imp",
            "runner:pkufarm", "yuki",
            pattern=None, force=False)
        assert result == {"transferred": ["a.txt"]}


@pytest.mark.parametrize("status", ["finished", "coda", "failed"])
def test_task_update_workflow_status_refreshes_distribution(status):
    """Terminal workflows refresh every non-algorithm job's registry."""
    from Yuki.server import tasks
    task_job = mock.Mock()
    task_job.job_type.return_value = "task"
    task_job.uuid = "imp1"
    task_job.is_input = True
    algo_job = mock.Mock()
    algo_job.job_type.return_value = "algorithm"
    algo_job.uuid = "imp2"
    workflow = mock.Mock()
    workflow.jobs = [task_job, algo_job]
    workflow.machine_id = "runner-1"
    workflow.status.return_value = status

    with mock.patch.object(tasks, "VWorkflow") as vwf, \
            mock.patch.object(tasks, "ImpressionStorage", create=True) as ims:
        vwf.create.return_value = workflow
        tasks.task_update_workflow_status("proj", "wf-1")

    ims.assert_called_once_with("proj", "imp1")
    ims.return_value.update_distribution.assert_called_once_with(
        refresh_cache=True, cache_runner_id="runner-1")


def test_task_update_workflow_status_no_refresh_while_running():
    """A non-terminal workflow leaves the registry alone."""
    from Yuki.server import tasks
    workflow = mock.Mock()
    workflow.jobs = []
    workflow.status.return_value = "running"

    with mock.patch.object(tasks, "VWorkflow") as vwf, \
            mock.patch.object(tasks, "ImpressionStorage", create=True) as ims:
        vwf.create.return_value = workflow
        tasks.task_update_workflow_status("proj", "wf-1")

    ims.assert_not_called()


def test_task_update_workflow_status_survives_refresh_failure():
    """A failing refresh never fails the status task itself."""
    from Yuki.server import tasks
    task_job = mock.Mock()
    task_job.job_type.return_value = "task"
    task_job.uuid = "imp1"
    workflow = mock.Mock()
    workflow.jobs = [task_job]
    workflow.machine_id = "runner-1"
    workflow.status.return_value = "failed"

    with mock.patch.object(tasks, "VWorkflow") as vwf, \
            mock.patch.object(tasks, "ImpressionStorage", create=True) as ims:
        vwf.create.return_value = workflow
        ims.return_value.update_distribution.side_effect = OSError("boom")
        tasks.task_update_workflow_status("proj", "wf-1")  # no raise
