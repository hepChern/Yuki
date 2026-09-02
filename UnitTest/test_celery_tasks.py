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


def test_task_update_workflow_status_delegates_to_workflow():
    """The task refreshes status via the workflow's own status write.

    Distribution refresh on the terminal transition happens inside
    update_workflow_status, not separately in the task.
    """
    from Yuki.server import tasks
    workflow = mock.Mock()

    with mock.patch.object(tasks, "VWorkflow") as vwf:
        vwf.create.return_value = workflow
        tasks.task_update_workflow_status("proj", "wf-1")

    vwf.create.assert_called_once_with("proj", [], "wf-1")
    workflow.update_workflow_status.assert_called_once_with()


@pytest.mark.parametrize("status", ["finished", "coda", "failed"])
def test_refresh_workflow_distributions_terminal(status):
    """Terminal workflows refresh every non-algorithm job's registry."""
    from Yuki.kernel.impression_storage import refresh_workflow_distributions
    task_job = mock.Mock()
    task_job.job_type.return_value = "task"
    task_job.uuid = "imp1"
    task_job.is_input = False
    algo_job = mock.Mock()
    algo_job.job_type.return_value = "algorithm"
    algo_job.uuid = "imp2"
    workflow = mock.Mock()
    workflow.jobs = [task_job, algo_job]
    workflow.machine_id = "runner-1"

    with mock.patch("Yuki.kernel.impression_storage.ImpressionStorage") as ims:
        refresh_workflow_distributions("proj", workflow, status)

    ims.assert_called_once_with("proj", "imp1")
    ims.return_value.update_distribution.assert_called_once_with(
        refresh_cache=True, cache_runner_id="runner-1")


def test_refresh_workflow_distributions_no_refresh_while_running():
    """A non-terminal workflow status leaves the registry alone."""
    from Yuki.kernel.impression_storage import refresh_workflow_distributions
    workflow = mock.Mock()
    workflow.jobs = []

    with mock.patch("Yuki.kernel.impression_storage.ImpressionStorage") as ims:
        refresh_workflow_distributions("proj", workflow, "running")

    ims.assert_not_called()


def test_refresh_workflow_distributions_survives_failure():
    """A failing refresh never fails the status update."""
    from Yuki.kernel.impression_storage import refresh_workflow_distributions
    task_job = mock.Mock()
    task_job.job_type.return_value = "task"
    task_job.uuid = "imp1"
    task_job.is_input = False
    workflow = mock.Mock()
    workflow.jobs = [task_job]
    workflow.machine_id = "runner-1"

    with mock.patch("Yuki.kernel.impression_storage.ImpressionStorage") as ims:
        ims.return_value.update_distribution.side_effect = OSError("boom")
        refresh_workflow_distributions("proj", workflow, "failed")  # no raise
