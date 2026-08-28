"""Unit tests for the CelebiChrono client's detailed-status surfacing.

The client (CelebiChrono) has no test suite of its own; these tests live in
Yuki's UnitTest package, which already depends on CelebiChrono.
"""
# pylint: disable=protected-access
import unittest
from unittest.mock import MagicMock, patch

from CelebiChrono.kernel.vobj_execution import ExecutionManagement, CHERN_CACHE
from CelebiChrono.kernel.vobj_file_display import FileManagementDisplay
from CelebiChrono.kernel.chern_communicator import ChernCommunicator


class _FakeObject:  # pylint: disable=too-many-instance-attributes
    """Bare ExecutionManagement-shaped object with stubbed dependencies."""

    def __init__(self, path, is_task=True):
        self.path = path
        self._is_task = is_task
        self.config_file = MagicMock()
        self.config_file.read_variable = MagicMock(return_value=[])
        self.impression_obj = MagicMock()
        self.impression_obj.uuid = "imp" + "1" * 31

    def is_task_or_algorithm(self):
        """Stub: task/algorithm unless configured otherwise."""
        return self._is_task

    def impression(self):
        """Stub: return the fake impression."""
        return self.impression_obj


class TestJobStatusDetail(unittest.TestCase):
    """ExecutionManagement.job_status surfaces detailed_status alongside."""

    def setUp(self):
        # Force real methods onto the fake instance.
        self.obj = _FakeObject("/fake/task/path")
        self.obj.is_task_or_algorithm = MagicMock(
            side_effect=self.obj.is_task_or_algorithm)
        self.obj.impression = MagicMock(side_effect=self.obj.impression)
        # job_status_detail falls back to job_status when the cache is cold;
        # bind the real method so the fake exercises that path.
        self.obj.job_status = MagicMock(
            side_effect=lambda cid, runner=None:
                ExecutionManagement.job_status(self.obj, cid, runner))

        self.cache = CHERN_CACHE
        self.cache.job_status_consult_table = {}

        self.cc = MagicMock()
        self.cc.job_status.return_value = {
            "status_legacy": "failed",
            "status_musical": "failed",
            "detailed_status": "Blocked: upstream input 1942c84f is failed",
        }
        self._cc_patcher = patch.object(
            ChernCommunicator, "instance", return_value=self.cc)
        self._cc_patcher.start()

    def tearDown(self):
        self._cc_patcher.stop()
        self.cache.job_status_consult_table = {}

    def test_job_status_detail_returns_cached_detailed_status(self):
        """After a status fetch, the detail is available without a new request."""
        status = ExecutionManagement.job_status(self.obj, 12345)
        self.assertEqual(status, "failed")

        detail = ExecutionManagement.job_status_detail(self.obj, 12345)
        self.assertEqual(detail, "Blocked: upstream input 1942c84f is failed")
        self.cc.job_status.assert_called_once()

    def test_job_status_detail_fetches_when_not_cached(self):
        """Requesting the detail alone triggers the underlying fetch."""
        detail = ExecutionManagement.job_status_detail(self.obj, 67890)
        self.assertEqual(detail, "Blocked: upstream input 1942c84f is failed")

    def test_job_status_detail_empty_when_server_sends_none(self):
        """A plain status response yields an empty detail string."""
        self.cc.job_status.return_value = {"status_legacy": "pending"}
        ExecutionManagement.job_status(self.obj, 11111)
        self.assertEqual(
            ExecutionManagement.job_status_detail(self.obj, 11111), "")


class TestStatusDisplayDetail(unittest.TestCase):
    """Directory status output appends the reason for failed subobjects."""

    def setUp(self):
        self.display = _FakeObject("/fake/dir/path", is_task=False)
        self.display.is_task_or_algorithm = MagicMock(
            side_effect=self.display.is_task_or_algorithm)
        self.display.impression = MagicMock(side_effect=self.display.impression)
        self.display.invariant_path = MagicMock(return_value="dir/path")
        self.display.status = MagicMock(return_value="impressed")
        self.display.job_status = MagicMock(return_value="failed")

        self.cache = CHERN_CACHE
        self.cache.job_status_consult_table = {}

        self.cc = MagicMock()
        self.cc.dite_status.return_value = "connected"
        self._cc_patcher = patch.object(
            ChernCommunicator, "instance", return_value=self.cc)
        self._cc_patcher.start()

        # One failed sub-task with a detail, one finished without.
        failed = _FakeObject("/fake/dir/path/merged_6_variables")
        finished = _FakeObject("/fake/dir/path/merged_12_variables")
        self.display.sub_objects = MagicMock(
            return_value=[failed, finished])
        self._sub_statuses = {
            failed.path: ("failed", "Blocked: upstream input 1942c84f is failed"),
            finished.path: ("finished", ""),
        }

        for sub in self.display.sub_objects():
            sub.job_status = MagicMock(
                side_effect=lambda cid, r=None, s=sub: self._sub_statuses[s.path][0])
            sub.job_status_detail = MagicMock(
                return_value=self._sub_statuses[sub.path][1])

    def tearDown(self):
        self._cc_patcher.stop()
        self.cache.job_status_consult_table = {}

    def test_failed_subobject_shows_detail_line(self):
        """The failed subobject's line carries its detailed status message."""
        message = FileManagementDisplay.printed_status(self.display)

        text = str(message)
        self.assertIn("[failed]", text)
        self.assertIn("Blocked: upstream input 1942c84f is failed", text)
        self.assertIn("[finished]", text)


if __name__ == "__main__":
    unittest.main()
