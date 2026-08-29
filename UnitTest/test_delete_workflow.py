"""Tests for workflow workspace deletion (delete_workspace + routes)."""
# pylint: disable=protected-access
import os
import json
from unittest import mock

import pytest


def test_vworkflow_delete_workspace_not_implemented():
    """The base workflow has no generic way to delete a workspace."""
    from Yuki.kernel.vworkflow import VWorkflow

    class _ConcreteVWorkflow(VWorkflow):
        """Concrete subclass so the abstract base can be instantiated."""

        def _execute_backend(self):
            return None

        def _sync_external_job_status(self, job):
            return None

        def update_workflow_status(self):
            return None

    workflow = _ConcreteVWorkflow.__new__(_ConcreteVWorkflow)
    with pytest.raises(NotImplementedError):
        workflow.delete_workspace()


def test_ssh_delete_workspace_removes_remote_dir():
    """The remote workspace is deleted with a quoted rm -rf command."""
    from Yuki.kernel.ssh_workflow import SshWorkflow
    workflow = SshWorkflow.__new__(SshWorkflow)
    workflow.remote_exec_path = "/remote/workflows/proj/wf one"
    workflow.logger = lambda msg: None

    ssh = mock.MagicMock()
    ssh.__enter__.return_value = ssh
    ssh.__exit__.return_value = False
    ssh.exec.return_value = ("", "", 0)
    workflow._ssh = mock.MagicMock(return_value=ssh)

    workflow.delete_workspace()

    ssh.exec.assert_called_once()
    cmd = ssh.exec.call_args[0][0]
    assert cmd == "rm -rf '/remote/workflows/proj/wf one'"
    assert ssh.exec.call_args[1]["timeout"] == 3600


def test_ssh_delete_workspace_failure_raises():
    """A nonzero remote exit code surfaces as a RuntimeError."""
    from Yuki.kernel.ssh_workflow import SshWorkflow
    workflow = SshWorkflow.__new__(SshWorkflow)
    workflow.remote_exec_path = "/remote/workflows/proj/wf1"
    workflow.logger = lambda msg: None

    ssh = mock.MagicMock()
    ssh.__enter__.return_value = ssh
    ssh.__exit__.return_value = False
    ssh.exec.return_value = ("", "no such file", 1)
    workflow._ssh = mock.MagicMock(return_value=ssh)

    with pytest.raises(RuntimeError):
        workflow.delete_workspace()


def test_native_delete_workspace_removes_local_dir(tmp_path):
    """The local execution workspace is removed."""
    from Yuki.kernel.native_workflow import NativeWorkflow
    workflow = NativeWorkflow.__new__(NativeWorkflow)
    workflow.local_exec_path = str(tmp_path / "wf1")
    workflow.logger = lambda msg: None
    os.makedirs(workflow.local_exec_path)
    with open(os.path.join(workflow.local_exec_path, "a.done"), "w",
              encoding="utf-8") as f:
        f.write("x")

    workflow.delete_workspace()

    assert not os.path.exists(workflow.local_exec_path)


def test_native_delete_workspace_missing_dir_no_raise(tmp_path):
    """Deleting an already-gone workspace does not raise."""
    from Yuki.kernel.native_workflow import NativeWorkflow
    workflow = NativeWorkflow.__new__(NativeWorkflow)
    workflow.local_exec_path = str(tmp_path / "gone")
    workflow.logger = lambda msg: None

    workflow.delete_workspace()  # no raise
