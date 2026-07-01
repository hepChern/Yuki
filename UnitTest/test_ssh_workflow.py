"""Unit tests for SshWorkflow remote backend."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class _MockSftp:
    """Minimal in-memory SFTP double for SshWorkflow tests."""

    def __init__(self):
        self.files = {}
        self.dirs = set()

    def mkdir(self, path):
        self.dirs.add(path)

    def chmod(self, path, _mode):
        pass

    def close(self):
        pass

    def put(self, local_path, remote_path):
        with open(local_path, "rb") as f:
            self.files[remote_path] = f.read()

    def get(self, remote_path, local_path):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(self.files[remote_path])

    def file(self, remote_path, mode="r"):
        class _File:
            def __init__(self, store, path):
                self._store = store
                self._path = path
                self._data = b""
            def write(self, data):
                self._data += data if isinstance(data, bytes) else data.encode("utf-8")
            def close(self):
                self._store[self._path] = self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
                return False
        if mode == "w":
            return _File(self.files, remote_path)
        raise NotImplementedError

    def listdir(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        entries = []
        for name in self.files:
            if name.startswith(path + "/"):
                rel = name[len(path) + 1:]
                if "/" not in rel:
                    entries.append(rel)
        return entries

    def stat(self, path):
        if path in self.dirs:
            from stat import S_IFDIR
            class _Stat:
                st_mode = S_IFDIR
                st_size = 0
            return _Stat()
        if path in self.files:
            from stat import S_IFREG
            class _Stat:
                st_mode = S_IFREG
                st_size = len(self.files[path])
            return _Stat()
        raise FileNotFoundError(path)

    def remove(self, path):
        self.files.pop(path, None)


class _MockChannel:
    def __init__(self, exit_code=0):
        self._exit_code = exit_code

    def recv_exit_status(self):
        return self._exit_code


class _MockStdout:
    def __init__(self, text, exit_code=0):
        self._text = text
        self.channel = _MockChannel(exit_code)

    def read(self):
        return self._text.encode("utf-8")


class _MockStderr:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text.encode("utf-8")


class TestSshWorkflow(unittest.TestCase):
    """Test SshWorkflow with an in-memory Paramiko mock."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._home_patcher = patch.dict(os.environ, {"HOME": self.tmpdir})
        self._home_patcher.start()

        self.project_uuid = "p" * 32
        self.workflow_uuid = "w" * 32

        # Write SSH config for the fake runner.
        self._write_ssh_config()

        from Yuki.kernel.ssh_workflow import SshWorkflow
        self.workflow = SshWorkflow(self.project_uuid, [], None)
        self.workflow.uuid = self.workflow_uuid
        self.workflow.machine_id = "runner-uuid"
        self.workflow.ssh_config = {
            "host": "remote.host",
            "user": "alice",
            "key_path": "~/.ssh/id_rsa",
            "port": 22,
            "remote_workdir": "/tmp/yuki-workflows",
        }
        self.workflow.remote_exec_path = f"/tmp/yuki-workflows/{self.workflow_uuid}"
        self.workflow.jobs = []

        self.mock_client = MagicMock()
        self.mock_sftp = _MockSftp()
        self.mock_client.open_sftp.return_value = self.mock_sftp

    def tearDown(self):
        self._home_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_ssh_config(self):
        config_path = os.path.join(self.tmpdir, ".Yuki", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({
                "runners_id": {"myrunner": "runner-uuid"},
                "backend_types": {"runner-uuid": "ssh"},
                "ssh_hosts": {"runner-uuid": "remote.host"},
                "ssh_users": {"runner-uuid": "alice"},
                "ssh_key_paths": {"runner-uuid": "~/.ssh/id_rsa"},
                "ssh_ports": {"runner-uuid": 22},
                "remote_workdirs": {"runner-uuid": "/tmp/yuki-workflows"},
            }, f)

    def _make_job(self, uuid_full, status_value="prelude",
                  is_input=False, job_type_value="task", files=None):
        job = MagicMock()
        job.uuid = uuid_full
        job.is_input = is_input
        job.path = "/fake/" + uuid_full
        job.job_type.return_value = job_type_value
        job.status.return_value = status_value
        job.short_uuid.return_value = uuid_full[:7]
        job.files.return_value = files or []
        job.environment.return_value = "docker.io/reanahub/reana-env-root6:6.18.04"
        return job

    @patch("paramiko.SSHClient")
    def test_execute_backend_uploads_snakefile_and_starts_command(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        self.mock_client.exec_command.return_value = (
            MagicMock(), _MockStdout("12345"), _MockStderr("")
        )

        # Prepare a local Snakefile to upload.
        snakefile_path = os.path.join(self.workflow.path, "Snakefile")
        os.makedirs(self.workflow.path, exist_ok=True)
        with open(snakefile_path, "w", encoding="utf-8") as f:
            f.write("rule test: shell: 'echo ok'")
        self.workflow.snakefile_path = snakefile_path

        self.workflow._execute_backend()

        remote_snakefile = f"{self.workflow.remote_exec_path}/Snakefile"
        self.assertIn(remote_snakefile, self.mock_sftp.files)
        self.mock_client.exec_command.assert_called()
        cmd = self.mock_client.exec_command.call_args[0][0]
        self.assertIn("yuki_run.sh", cmd)

    @patch("paramiko.SSHClient")
    def test_update_workflow_status_without_local_workflow_info(self, mock_ssh_cls):
        """SSH status update must work without local workflow_info.json.

        workflow_info.json is uploaded to the remote host by
        _create_remote_structure; it is not written locally. The status update
        should derive the job list from self.jobs (loaded from local
        config.json jobs_info) instead of requiring the remote-only file.
        """
        from Yuki.kernel.status_constants import CODA
        mock_ssh_cls.return_value = self.mock_client

        job = self._make_job("a" * 32)
        self.workflow.jobs = [job]
        # Ensure the local workflow directory exists (setUp already creates it).
        os.makedirs(self.workflow.path, exist_ok=True)

        done_path = f"{self.workflow.remote_exec_path}/{job.short_uuid()}.done"
        self.mock_sftp.files[done_path] = b""
        self.mock_sftp.dirs.add(self.workflow.remote_exec_path)

        self.workflow.update_workflow_status()

        results_path = os.path.join(self.workflow.path, "results.json")
        self.assertTrue(os.path.exists(results_path))
        results = json.load(open(results_path, encoding="utf-8"))
        self.assertEqual(results["results"]["status"], "finished")
        job.set_status.assert_called_with("finished", "Remote execution completed")

    @patch("paramiko.SSHClient")
    def test_update_workflow_status_reports_finished_when_done_files_exist(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        self.mock_sftp.dirs.add(self.workflow.remote_exec_path)
        short = "a" * 7
        self.mock_sftp.files[f"{self.workflow.remote_exec_path}/{short}.done"] = b""

        job = self._make_job("a" * 32)
        self.workflow.jobs = [job]
        os.makedirs(self.workflow.path, exist_ok=True)

        self.workflow.update_workflow_status()

        results_path = os.path.join(self.workflow.path, "results.json")
        results = json.load(open(results_path, encoding="utf-8"))
        self.assertEqual(results["results"]["status"], "finished")
        self.assertEqual(results["results"]["progress"]["completed"], 1)

    @patch("paramiko.SSHClient")
    def test_propagate_done_jobs_become_finished(self, mock_ssh_cls):
        """Completed jobs should be stored with the legacy status 'finished'
        so the client can display [coda][finished]."""
        mock_ssh_cls.return_value = self.mock_client
        self.mock_sftp.dirs.add(self.workflow.remote_exec_path)

        job = self._make_job("a" * 32)
        self.workflow.jobs = [job]
        self.mock_sftp.files[f"{self.workflow.remote_exec_path}/{job.short_uuid()}.done"] = b""

        self.workflow.propagate_job_statuses(workflow_terminal=False)

        job.set_status.assert_called_once_with("finished", "Remote execution completed")

    @patch("paramiko.SSHClient")
    def test_download_outputs_pulls_remote_stageout_files(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        short = "a" * 7
        remote_dir = f"{self.workflow.remote_exec_path}/imp{short}/stageout"
        self.mock_sftp.dirs.add(remote_dir)
        self.mock_sftp.files[f"{remote_dir}/output.root"] = b"data"

        self.workflow.download_outputs(impression="a" * 32)

        local_file = os.path.join(
            self.tmpdir, ".Yuki", "Storage", self.project_uuid,
            "a" * 32, "runner-uuid", "stageout", "output.root"
        )
        self.assertTrue(os.path.exists(local_file))
        with open(local_file, "rb") as f:
            self.assertEqual(f.read(), b"data")

    @patch("paramiko.SSHClient")
    def test_kill_sends_signal_to_remote_pid(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        self.mock_sftp.files[f"{self.workflow.remote_exec_path}/yuki.pid"] = b"12345"

        def exec_side_effect(command, timeout=300):
            if command.startswith("cat"):
                return MagicMock(), _MockStdout("12345"), _MockStderr("")
            return MagicMock(), _MockStdout(""), _MockStderr("")

        self.mock_client.exec_command.side_effect = exec_side_effect

        self.workflow.kill()

        cmds = [call[0][0] for call in self.mock_client.exec_command.call_args_list]
        self.assertTrue(any("kill 12345" in cmd for cmd in cmds))

    @patch("paramiko.SSHClient")
    def test_ping_returns_true_when_remote_echo_succeeds(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        self.mock_client.exec_command.return_value = (
            MagicMock(), _MockStdout("ok"), _MockStderr("")
        )

        self.assertTrue(self.workflow.ping())

    @patch("paramiko.SSHClient")
    def test_ping_returns_false_on_connection_failure(self, mock_ssh_cls):
        mock_ssh_cls.return_value = self.mock_client
        self.mock_client.connect.side_effect = OSError("Connection refused")

        self.assertFalse(self.workflow.ping())

    def _prepare_snakefile(self):
        """Create a local Snakefile so _upload_files_remote has one to upload."""
        os.makedirs(self.workflow.path, exist_ok=True)
        snakefile_path = os.path.join(self.workflow.path, "Snakefile")
        with open(snakefile_path, "w", encoding="utf-8") as f:
            f.write("rule all:\n    shell: 'true'\n")
        self.workflow.snakefile_path = snakefile_path

    @patch("paramiko.SSHClient")
    def test_upload_files_remote_uploads_input_stageout_files(self, mock_ssh_cls):
        """Input-job data in local Storage must be copied to the remote stageout.

        Downstream jobs resolve their inputs through a ``gen -> ../imp<short>``
        symlink, so the producer's ``imp<short>/stageout/<file>`` must exist on
        the remote host before Snakemake runs. Recording it in a manifest is not
        enough: nothing processes that manifest remotely.
        """
        mock_ssh_cls.return_value = self.mock_client

        job = self._make_job("a" * 32, is_input=True)
        job.machine_id = "runner-uuid"
        self.workflow.jobs = [job]

        src_stageout = os.path.join(
            self.tmpdir, ".Yuki", "Storage", self.project_uuid,
            "a" * 32, "runner-uuid", "stageout",
        )
        os.makedirs(src_stageout, exist_ok=True)
        with open(os.path.join(src_stageout, "data.root"), "wb") as f:
            f.write(b"input-bytes")

        self._prepare_snakefile()

        self.workflow._upload_files_remote()

        expected = f"{self.workflow.remote_exec_path}/impaaaaaaa/stageout/data.root"
        self.assertIn(expected, self.mock_sftp.files)
        self.assertEqual(self.mock_sftp.files[expected], b"input-bytes")

    @patch("paramiko.SSHClient")
    def test_upload_files_remote_uploads_rawdata_stageout_files(self, mock_ssh_cls):
        """Rawdata files must be copied to the remote ``imp<short>/stageout`` dir."""
        mock_ssh_cls.return_value = self.mock_client

        job = self._make_job("b" * 32)
        job.environment.return_value = "rawdata"
        job.path = os.path.join(self.tmpdir, "jobs", "b" * 32)
        self.workflow.jobs = [job]

        rawdata_dir = os.path.join(job.path, "rawdata")
        os.makedirs(rawdata_dir, exist_ok=True)
        with open(os.path.join(rawdata_dir, "raw.txt"), "wb") as f:
            f.write(b"raw-bytes")

        self._prepare_snakefile()

        self.workflow._upload_files_remote()

        expected = f"{self.workflow.remote_exec_path}/impbbbbbbb/stageout/raw.txt"
        self.assertIn(expected, self.mock_sftp.files)
        self.assertEqual(self.mock_sftp.files[expected], b"raw-bytes")


if __name__ == "__main__":
    unittest.main()
