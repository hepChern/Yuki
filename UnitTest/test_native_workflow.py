"""Unit tests for NativeWorkflow per-job status propagation."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestNativeWorkflowPropagation(unittest.TestCase):
    """Test NativeWorkflow.propagate_job_statuses and _read_job_log_tail."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Patch HOME so NativeWorkflow rooted file ops land in tmpdir.
        self._home_patcher = patch.dict(os.environ, {"HOME": self.tmpdir})
        self._home_patcher.start()

        # Use fixed-length fake UUIDs (32 chars) for predictable short_uuid.
        self.project_uuid = "p" * 32
        self.workflow_uuid = "w" * 32

        # Construct NativeWorkflow against the temp HOME.
        from Yuki.kernel.native_workflow import NativeWorkflow
        self.workflow = NativeWorkflow(self.project_uuid, [], None)
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

    def test_propagate_missing_done_terminal_becomes_failed(self):
        from Yuki.kernel.status_constants import FAILED
        short = "a" * 7
        # 32-char uuid whose first 7 chars match `short`.
        job_uuid = short + "z" * 25
        job = self._make_job(job_uuid)
        self.workflow.jobs = [job]

        self._write_user_log(
            short, 0,
            "running step\nTraceback (most recent call last):\n  ZeroDivisionError\n",
        )

        self.workflow.propagate_job_statuses(workflow_terminal=True)

        self.assertEqual(job.set_status.call_count, 1)
        status_arg, detail_arg = job.set_status.call_args.args
        self.assertEqual(status_arg, FAILED)
        self.assertIn("Local execution failed", detail_arg)
        self.assertIn("ZeroDivisionError", detail_arg)

    def test_propagate_skips_input_and_algorithm_jobs(self):
        input_job = self._make_job("a" * 32, is_input=True)
        algo_job = self._make_job("b" * 32, job_type_value="algorithm")
        self.workflow.jobs = [input_job, algo_job]

        # Both have .done — propagation would otherwise promote them.
        self._touch_done(input_job.short_uuid())
        self._touch_done(algo_job.short_uuid())

        self.workflow.propagate_job_statuses(workflow_terminal=True)

        input_job.set_status.assert_not_called()
        algo_job.set_status.assert_not_called()

    def test_propagate_does_not_churn_terminal_status(self):
        from Yuki.kernel.status_constants import CODA, FINAL_NOTE, FAILED, STOPPED, DELETED
        coda_job = self._make_job("a" * 32, status_value=CODA)
        final_job = self._make_job("b" * 32, status_value=FINAL_NOTE)
        failed_job = self._make_job("c" * 32, status_value=FAILED)
        stopped_job = self._make_job("d" * 32, status_value=STOPPED)
        deleted_job = self._make_job("e" * 32, status_value=DELETED)
        self.workflow.jobs = [coda_job, final_job, failed_job, stopped_job, deleted_job]

        # All have .done so propagate WOULD touch them otherwise.
        for j in self.workflow.jobs:
            self._touch_done(j.short_uuid())

        self.workflow.propagate_job_statuses(workflow_terminal=True)

        coda_job.set_status.assert_not_called()
        final_job.set_status.assert_not_called()
        failed_job.set_status.assert_not_called()
        stopped_job.set_status.assert_not_called()
        deleted_job.set_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
