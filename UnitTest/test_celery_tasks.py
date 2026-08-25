"""Tests for Yuki Celery tasks."""
from unittest import mock


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
