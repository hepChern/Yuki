# Dry workflow status propagation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `NativeWorkflow` reconcile each `VJob`'s on-disk status with the marker files left by snakemake, and surface failures with a tail of the user log so operators can see what went wrong.

**Architecture:** Add one method `NativeWorkflow.propagate_job_statuses(workflow_terminal: bool)` plus a small helper `_read_job_log_tail`. Both `NativeWorkflow.update_workflow_status` (server path) and `SnakemakeMonitor` (CLI path) call it. Also add `--keep-going` to the snakemake invocation so independent failures don't poison unrelated jobs.

**Tech Stack:** Python 3.9+, `unittest.TestCase`, `unittest.mock`, pytest as runner. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-17-native-workflow-status-propagation-design.md`](../specs/2026-05-17-native-workflow-status-propagation-design.md)

---

### Task 1: Test scaffolding

**Files:**
- Create: `UnitTest/test_native_workflow.py`

This task lays down the test file with a working setUp/tearDown that constructs a real `NativeWorkflow` against a temp `$HOME`, and a single trivial test that confirms the scaffolding runs. Later tasks add behaviour-driven tests on top of this.

- [ ] **Step 1: Create the test file with scaffolding**

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the scaffolding test**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: `test_scaffolding_constructs_workflow PASSED` (1 passed)

- [ ] **Step 3: Commit**

```bash
git add UnitTest/test_native_workflow.py
git commit -m "test(native-workflow): scaffold per-job propagation test suite"
```

---

### Task 2: `propagate_job_statuses` — promote `.done` jobs to CODA

TDD: write the test first, see it fail, then implement the minimal method.

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (add `propagate_job_statuses`)
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `TestNativeWorkflowPropagation` in `UnitTest/test_native_workflow.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py::TestNativeWorkflowPropagation::test_propagate_done_jobs_become_coda -v`
Expected: FAIL with `AttributeError: 'NativeWorkflow' object has no attribute 'propagate_job_statuses'`

- [ ] **Step 3: Implement minimal method**

In `Yuki/kernel/native_workflow.py`, add this method to the `NativeWorkflow` class (place it after `_resolve_conda_environment` for now; final placement doesn't matter as long as it's a method on the class):

```python
    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with on-disk markers.

        See spec at docs/superpowers/specs/2026-05-17-native-workflow-status-propagation-design.md
        for the full classification table.
        """
        from .status_constants import CODA

        for job in self.jobs:
            short = job.short_uuid()
            done_path = os.path.join(self.local_exec_path, f"{short}.done")
            if os.path.exists(done_path):
                job.set_status(CODA, "Local execution completed")
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 2 passed (scaffolding + new test).

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): propagate .done markers to per-job CODA status"
```

---

### Task 3: In-flight runs leave jobs unchanged

The "missing `.done` while workflow is still running" path must NOT mark anything FAILED. Verify the current implementation already preserves this, then add the regression test.

**Files:**
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Add the test**

```python
    def test_propagate_in_flight_leaves_jobs_unchanged(self):
        job = self._make_job("a" * 32, status_value="in movement")
        self.workflow.jobs = [job]
        # No .done file written.

        self.workflow.propagate_job_statuses(workflow_terminal=False)

        job.set_status.assert_not_called()
```

- [ ] **Step 2: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add UnitTest/test_native_workflow.py
git commit -m "test(native-workflow): in-flight propagation leaves jobs untouched"
```

---

### Task 4: Terminal-state, missing `.done`, no logs → FAILED with skip message

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (extend `propagate_job_statuses`)
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py::TestNativeWorkflowPropagation::test_propagate_missing_done_no_logs_becomes_failed_with_skip_message -v`
Expected: FAIL — `set_status` was not called.

- [ ] **Step 3: Extend the method**

Replace the body of `propagate_job_statuses` with:

```python
    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with on-disk markers.

        See spec at docs/superpowers/specs/2026-05-17-native-workflow-status-propagation-design.md
        for the full classification table.
        """
        from .status_constants import CODA, FAILED

        for job in self.jobs:
            short = job.short_uuid()
            done_path = os.path.join(self.local_exec_path, f"{short}.done")
            if os.path.exists(done_path):
                job.set_status(CODA, "Local execution completed")
                continue

            if not workflow_terminal:
                continue

            logs_dir = os.path.join(self.local_exec_path, f"imp{short}", "logs")
            has_logs = os.path.isdir(logs_dir) and bool(os.listdir(logs_dir))
            if not has_logs:
                job.set_status(
                    FAILED,
                    "Skipped: upstream dependency failed before this job ran",
                )
```

- [ ] **Step 4: Run all tests to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): mark skipped-due-to-upstream jobs as FAILED"
```

---

### Task 5: Helper `_read_job_log_tail`

Add the log-tail helper used by the next task. Test it directly so its behaviour is locked in.

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (add `_read_job_log_tail`)
- Modify: `UnitTest/test_native_workflow.py` (add 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to the test class:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 2 new tests FAIL with `AttributeError: 'NativeWorkflow' object has no attribute '_read_job_log_tail'`.

- [ ] **Step 3: Implement the helper**

In `Yuki/kernel/native_workflow.py`, add to the `NativeWorkflow` class (place adjacent to `propagate_job_statuses`):

```python
    def _read_job_log_tail(self, short_uuid, max_chars=500):
        """Return tail of the highest-indexed celebi_user_step*.log for a job.

        Returns "" when no logs directory exists or it contains no matching
        files. The highest step index is the most recent output and is where
        the failure usually surfaced.
        """
        import re

        logs_dir = os.path.join(self.local_exec_path, f"imp{short_uuid}", "logs")
        if not os.path.isdir(logs_dir):
            return ""

        pattern = re.compile(r"^celebi_user_step(\d+)\.log$")
        candidates = []
        for fname in os.listdir(logs_dir):
            m = pattern.match(fname)
            if m:
                candidates.append((int(m.group(1)), fname))

        if not candidates:
            return ""

        candidates.sort(reverse=True)
        latest = candidates[0][1]
        log_path = os.path.join(logs_dir, latest)

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return ""
        return content[-max_chars:]
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): add _read_job_log_tail helper"
```

---

### Task 6: Terminal-state, missing `.done`, logs present → FAILED with log tail

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (extend `propagate_job_statuses`)
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py::TestNativeWorkflowPropagation::test_propagate_missing_done_terminal_becomes_failed -v`
Expected: FAIL — set_status was called with the upstream-failure message instead.

- [ ] **Step 3: Extend propagate_job_statuses**

Replace the `if not has_logs:` block at the end of the method with this larger branch:

```python
            if has_logs:
                tail = self._read_job_log_tail(short)
                if tail:
                    detail = f"Local execution failed: {tail}"
                else:
                    detail = "Local execution failed"
                job.set_status(FAILED, detail)
            else:
                job.set_status(
                    FAILED,
                    "Skipped: upstream dependency failed before this job ran",
                )
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): mark failed jobs FAILED with stderr tail"
```

---

### Task 7: Skip `is_input` and `algorithm` jobs

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (extend `propagate_job_statuses`)
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py::TestNativeWorkflowPropagation::test_propagate_skips_input_and_algorithm_jobs -v`
Expected: FAIL — `set_status` was called.

- [ ] **Step 3: Add the skip filter**

Modify `propagate_job_statuses`. Insert these two `continue` statements at the very top of the `for job in self.jobs:` loop, before any other logic:

```python
        for job in self.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            # ... existing body ...
```

The full method now reads:

```python
    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with on-disk markers."""
        from .status_constants import CODA, FAILED

        for job in self.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue

            short = job.short_uuid()
            done_path = os.path.join(self.local_exec_path, f"{short}.done")
            if os.path.exists(done_path):
                job.set_status(CODA, "Local execution completed")
                continue

            if not workflow_terminal:
                continue

            logs_dir = os.path.join(self.local_exec_path, f"imp{short}", "logs")
            has_logs = os.path.isdir(logs_dir) and bool(os.listdir(logs_dir))
            if has_logs:
                tail = self._read_job_log_tail(short)
                if tail:
                    detail = f"Local execution failed: {tail}"
                else:
                    detail = "Local execution failed"
                job.set_status(FAILED, detail)
            else:
                job.set_status(
                    FAILED,
                    "Skipped: upstream dependency failed before this job ran",
                )
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): skip input and algorithm jobs in propagation"
```

---

### Task 8: Don't churn jobs already in a terminal state

`is_terminal_status` covers CODA, FINAL_NOTE, FAILED, STOPPED, DELETED. Make sure the method respects it so settled state isn't repeatedly rewritten.

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (add guard)
- Modify: `UnitTest/test_native_workflow.py` (add test)

- [ ] **Step 1: Write the failing test**

```python
    def test_propagate_does_not_churn_terminal_status(self):
        from Yuki.kernel.status_constants import CODA, FINAL_NOTE, FAILED
        coda_job = self._make_job("a" * 32, status_value=CODA)
        final_job = self._make_job("b" * 32, status_value=FINAL_NOTE)
        failed_job = self._make_job("c" * 32, status_value=FAILED)
        self.workflow.jobs = [coda_job, final_job, failed_job]

        # All have .done so propagate WOULD touch them otherwise.
        for j in self.workflow.jobs:
            self._touch_done(j.short_uuid())

        self.workflow.propagate_job_statuses(workflow_terminal=True)

        coda_job.set_status.assert_not_called()
        final_job.set_status.assert_not_called()
        failed_job.set_status.assert_not_called()
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest UnitTest/test_native_workflow.py::TestNativeWorkflowPropagation::test_propagate_does_not_churn_terminal_status -v`
Expected: FAIL — `set_status` was called with `CODA, "Local execution completed"`.

- [ ] **Step 3: Add the guard**

Replace the body of `propagate_job_statuses` with the final form below. The change vs. Task 7 is the additional terminal-status `continue`:

```python
    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with on-disk markers."""
        from .status_constants import (
            CODA, FAILED, is_terminal_status, translate_to_musical
        )

        for job in self.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            if is_terminal_status(translate_to_musical(job.status())):
                continue

            short = job.short_uuid()
            done_path = os.path.join(self.local_exec_path, f"{short}.done")
            if os.path.exists(done_path):
                job.set_status(CODA, "Local execution completed")
                continue

            if not workflow_terminal:
                continue

            logs_dir = os.path.join(self.local_exec_path, f"imp{short}", "logs")
            has_logs = os.path.isdir(logs_dir) and bool(os.listdir(logs_dir))
            if has_logs:
                tail = self._read_job_log_tail(short)
                if tail:
                    detail = f"Local execution failed: {tail}"
                else:
                    detail = "Local execution failed"
                job.set_status(FAILED, detail)
            else:
                job.set_status(
                    FAILED,
                    "Skipped: upstream dependency failed before this job ran",
                )
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py UnitTest/test_native_workflow.py
git commit -m "feat(native-workflow): leave terminal-state jobs untouched in propagation"
```

---

### Task 9: Hook propagation into `update_workflow_status`

The server-polling path calls `update_workflow_status`. Add the propagation call so per-job status converges every time the API queries the workflow.

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (extend `update_workflow_status`)

This is wiring; the behaviour of `propagate_job_statuses` itself is already tested. No new unit test is added — the contract is "call propagate at the end of update_workflow_status with the right terminal flag", which is best verified by a manual smoke test (see Task 12).

- [ ] **Step 1: Modify `update_workflow_status`**

In `Yuki/kernel/native_workflow.py`, locate the existing `update_workflow_status` method. After the line:

```python
            results_file.write_variable("results", results)
```

(and BEFORE the surrounding `except Exception as e:` clause), append:

```python
            workflow_terminal = status in ("finished", "failed")
            self.propagate_job_statuses(workflow_terminal=workflow_terminal)
```

So the method's try-block ends with:

```python
            self.logger(f"[LOCAL] Workflow status: {status}, "
                         f"Progress: {results['progress']['completed']}/"
                         f"{results['progress']['total']}")

            path = os.path.join(self.path, "results.json")
            results_file = metadata.ConfigFile(path)
            results_file.write_variable("results", results)

            workflow_terminal = status in ("finished", "failed")
            self.propagate_job_statuses(workflow_terminal=workflow_terminal)

        except Exception as e:
            self.logger(f"[LOCAL] Failed to update workflow status: {e}")
```

- [ ] **Step 2: Run all tests to make sure nothing regresses**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add Yuki/kernel/native_workflow.py
git commit -m "feat(native-workflow): propagate per-job status from update_workflow_status"
```

---

### Task 10: Add `--keep-going` to snakemake invocation

So that independent failures don't poison unrelated jobs.

**Files:**
- Modify: `Yuki/kernel/snakemake_monitor.py` (one line in the snakemake command)

- [ ] **Step 1: Modify `execute_snakemake`**

In `Yuki/kernel/snakemake_monitor.py`, replace:

```python
        cmd = [
            "snakemake",
            "--use-conda",
            "--conda-frontend", "conda",
            "-j", str(cores)
        ]
```

with:

```python
        cmd = [
            "snakemake",
            "--use-conda",
            "--conda-frontend", "conda",
            "--keep-going",
            "-j", str(cores)
        ]
```

- [ ] **Step 2: Verify import still works**

Run: `python -c "from Yuki.kernel.snakemake_monitor import SnakemakeMonitor; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add Yuki/kernel/snakemake_monitor.py
git commit -m "feat(snakemake-monitor): pass --keep-going so independent failures isolate"
```

---

### Task 11: `SnakemakeMonitor` carries UUIDs and calls propagation

Give the CLI-side monitor everything it needs to construct a `NativeWorkflow` and call propagation when execution finishes (both success and failure paths).

**Files:**
- Modify: `Yuki/kernel/snakemake_monitor.py` (constructor + 2 call sites)

- [ ] **Step 1: Extend the constructor**

Replace the existing constructor in `Yuki/kernel/snakemake_monitor.py`:

```python
    def __init__(self, workflow_path, local_exec_path,
                 project_uuid=None, workflow_uuid=None):
        """
        Initialize snakemake monitor.

        Args:
            workflow_path: Path to ~/.Yuki/Workflows/<project>/<uuid>/
            local_exec_path: Path to ~/.Yuki/LocalWorkflows/<uuid>/
            project_uuid: Project UUID (needed to instantiate NativeWorkflow
                          for per-job status propagation). Optional; if
                          omitted, derived from workflow_path layout.
            workflow_uuid: Workflow UUID (same rationale).
        """
        self.workflow_path = workflow_path
        self.local_exec_path = local_exec_path
        self.results_file = os.path.join(workflow_path, "results.json")
        self.log_file = os.path.join(workflow_path, "log.json")
        self.snakemake_log = os.path.join(local_exec_path, "snakemake.log")
        self.snakemake_report = os.path.join(local_exec_path, "report.json")

        if workflow_uuid is None:
            workflow_uuid = os.path.basename(workflow_path.rstrip("/"))
        if project_uuid is None:
            project_uuid = os.path.basename(
                os.path.dirname(workflow_path.rstrip("/"))
            )
        self.project_uuid = project_uuid
        self.workflow_uuid = workflow_uuid
```

- [ ] **Step 2: Add a helper that calls propagation**

Add this method to `SnakemakeMonitor`:

```python
    def _propagate_per_job_status(self, logger=None):
        """Reconcile each VJob's status with on-disk markers."""
        try:
            from .vworkflow import VWorkflow
            workflow = VWorkflow.create(
                self.project_uuid, [],
                uuid=self.workflow_uuid, mode="native",
            )
            workflow.propagate_job_statuses(workflow_terminal=True)
        except Exception as e:
            if logger:
                logger(f"[SNAKEMAKE] Per-job propagation failed: {e}")
```

The try/except is defensive: a propagation failure must not flip a successful workflow run into a failure outcome.

- [ ] **Step 3: Call propagation from `_finalize_results`**

Find `_finalize_results` and add a call at the very end of its `try` block (just before the closing `except Exception as e:`):

```python
            if logger:
                logger(f"[SNAKEMAKE] Execution completed successfully")

            self._propagate_per_job_status(logger)

        except Exception as e:
            if logger:
                logger(f"[SNAKEMAKE] Error finalizing results: {e}")
```

- [ ] **Step 4: Call propagation from `_handle_failure`**

Find `_handle_failure` and add a call at the very end of its `try` block (just before the closing `except`):

```python
            if logger:
                logger(f"[SNAKEMAKE] Execution failed: {error_msg}")

            self._propagate_per_job_status(logger)

        except Exception as e:
            if logger:
                logger(f"[SNAKEMAKE] Error handling failure: {e}")
```

- [ ] **Step 5: Verify import still works**

Run: `python -c "from Yuki.kernel.snakemake_monitor import SnakemakeMonitor; m = SnakemakeMonitor('/tmp/w', '/tmp/l'); print(type(m).__name__)"`
Expected: prints `SnakemakeMonitor` (proves the constructor accepts the new signature and the path-derivation logic does not raise).

- [ ] **Step 6: Confirm existing tests still pass**

Run: `python -m pytest UnitTest/test_native_workflow.py -v`
Expected: 9 passed.

- [ ] **Step 7: Commit**

```bash
git add Yuki/kernel/snakemake_monitor.py
git commit -m "feat(snakemake-monitor): propagate per-job status on success and failure"
```

---

### Task 12: CLI passes UUIDs to `SnakemakeMonitor` and smoke-test the wiring

**Files:**
- Modify: `Yuki/main.py` (the `run-workflow` command)

- [ ] **Step 1: Update the `SnakemakeMonitor` construction**

In `Yuki/main.py`, locate the `run_workflow` Click command. Replace:

```python
    monitor = SnakemakeMonitor(workflow_path, local_exec_dir)
```

with:

```python
    monitor = SnakemakeMonitor(
        workflow_path, local_exec_dir,
        project_uuid=project_uuid,
        workflow_uuid=workflow_uuid,
    )
```

- [ ] **Step 2: Smoke-test the CLI entry point loads**

Run: `python -c "from Yuki.main import run_workflow; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest UnitTest/ -v`
Expected: all tests in `test_native_workflow.py` (9) and `test_env_interpreter.py` (19) pass — 28 passed in total. Other test files in `UnitTest/` are empty (`test_server.py`, `test_server_main.py`, `test_server_package.py`, `test_refactored_integration.py`) and remain empty after this change.

- [ ] **Step 4: Commit**

```bash
git add Yuki/main.py
git commit -m "feat(cli): pass uuids to SnakemakeMonitor for per-job propagation"
```

- [ ] **Step 5: Manual smoke test (operator-driven, not automated)**

Outside the scope of this plan to script: have the operator create a small project with two parallel jobs, mark one to fail (`exit 1`), run `yuki run-workflow <uuid>`, and verify:
- Failing job's `~/.Yuki/Storage/<project>/<job>/status.json` shows status `failed` with `detailed_status` containing a tail of `celebi_user_step*.log`.
- Parallel succeeding job's `status.json` shows status `coda` (proves `--keep-going` worked).
- Workflow-level `results.json` shows status `failed` with `progress.completed < progress.total`.

If the manual smoke test passes, the work is complete. If it fails, file the regression as a new task — the issue is most likely in the wiring of `_propagate_per_job_status` or in the path assumptions in `_make_job` / `short_uuid` semantics.
