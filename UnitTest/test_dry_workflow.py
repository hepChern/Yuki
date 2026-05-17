"""Unit tests for DryWorkflow per-job status propagation."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestDryWorkflowPropagation(unittest.TestCase):
    """Test DryWorkflow.propagate_job_statuses and _read_job_log_tail."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Patch HOME so DryWorkflow rooted file ops land in tmpdir.
        self._home_patcher = patch.dict(os.environ, {"HOME": self.tmpdir})
        self._home_patcher.start()

        # Use fixed-length fake UUIDs (32 chars) for predictable short_uuid.
        self.project_uuid = "p" * 32
        self.workflow_uuid = "w" * 32

        # Construct DryWorkflow against the temp HOME.
        from Yuki.kernel.dry_workflow import DryWorkflow
        self.workflow = DryWorkflow(self.project_uuid, [], None)
        # Override generated uuid -> known value so paths are predictable.
        self.workflow.uuid = self.workflow_uuid
        self.workflow.local_exec_path = os.path.join(
            self.tmpdir, ".Yuki", "LocalWorkflows", self.workflow_uuid
        )
        os.makedirs(self.workflow.local_exec_path, exist_ok=True)
        self.workflow.jobs = []

    def tearDown(self):
        self._home_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    def _make_job(self, uuid_full, status_value="prelude",
                  is_input=False, job_type_value="task"):
        """Build a MagicMock VJob with the methods propagate_job_statuses uses."""
        job = MagicMock()
        job.uuid = uuid_full
        job.is_input = is_input
        job.path = "/fake/" + uuid_full
        job.job_type.return_value = job_type_value
        # status() takes a 'musical' kwarg; we return the same value either way.
        job.status.return_value = status_value
        job.short_uuid.return_value = uuid_full[:7]
        return job

    def _touch_done(self, short_uuid):
        path = os.path.join(self.workflow.local_exec_path, short_uuid + ".done")
        with open(path, "w", encoding="utf-8"):
            pass
        return path

    def _write_user_log(self, short_uuid, step_index, content):
        logs_dir = os.path.join(
            self.workflow.local_exec_path, "imp" + short_uuid, "logs"
        )
        os.makedirs(logs_dir, exist_ok=True)
        path = os.path.join(logs_dir, f"celebi_user_step{step_index}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # -- scaffolding sanity check ----------------------------------------

    def test_scaffolding_constructs_workflow(self):
        self.assertTrue(os.path.isdir(self.workflow.local_exec_path))
        self.assertEqual(self.workflow.uuid, self.workflow_uuid)

    def test_propagate_done_jobs_become_coda(self):
        from Yuki.kernel.status_constants import CODA
        job_a = self._make_job("a" * 32)
        job_b = self._make_job("b" * 32)
        self.workflow.jobs = [job_a, job_b]

        self._touch_done(job_a.short_uuid())
        self._touch_done(job_b.short_uuid())

        self.workflow.propagate_job_statuses(workflow_terminal=False)

        job_a.set_status.assert_called_once_with(CODA, "Local execution completed")
        job_b.set_status.assert_called_once_with(CODA, "Local execution completed")

    def test_propagate_in_flight_leaves_jobs_unchanged(self):
        job = self._make_job("a" * 32, status_value="in movement")
        self.workflow.jobs = [job]
        # No .done file written.

        self.workflow.propagate_job_statuses(workflow_terminal=False)

        job.set_status.assert_not_called()

    def test_propagate_missing_done_no_logs_becomes_failed_with_skip_message(self):
        from Yuki.kernel.status_constants import FAILED
        job = self._make_job("a" * 32)
        self.workflow.jobs = [job]
        # No .done, no imp<short>/logs/ directory.

        self.workflow.propagate_job_statuses(workflow_terminal=True)

        job.set_status.assert_called_once_with(
            FAILED,
            "Skipped: upstream dependency failed before this job ran",
        )

    def test_read_job_log_tail_returns_empty_when_no_logs(self):
        # imp<short>/logs/ does not exist.
        tail = self.workflow._read_job_log_tail("a" * 7)
        self.assertEqual(tail, "")

    def test_read_job_log_tail_picks_highest_step_index(self):
        short = "a" * 7
        self._write_user_log(short, 0, "first step output")
        self._write_user_log(short, 1, "second step output")
        self._write_user_log(short, 2, "third step boom: traceback here")

        tail = self.workflow._read_job_log_tail(short)
        self.assertIn("third step boom", tail)
        self.assertNotIn("first step", tail)


if __name__ == "__main__":
    unittest.main()
