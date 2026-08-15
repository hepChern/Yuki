"""Unit tests for SshWorkflow remote backend."""
# pylint: disable=protected-access
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
        """Record the created remote directory."""
        self.dirs.add(path)

    def chmod(self, path, _mode):
        """No-op chmod."""

    def close(self):
        """No-op close."""

    def put(self, local_path, remote_path):
        """Read the local file into the in-memory store."""
        with open(local_path, "rb") as f:
            self.files[remote_path] = f.read()

    def get(self, remote_path, local_path):
        """Write the stored remote file to local_path."""
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(self.files[remote_path])

    def file(self, remote_path, mode="r"):
        """Open a writable handle into the in-memory store."""
        class _File:
            def __init__(self, store, path):
                self._store = store
                self._path = path
                self._data = b""
            def write(self, data):
                """Accumulate the uploaded bytes."""
                self._data += data if isinstance(data, bytes) else data.encode("utf-8")
            def close(self):
                """Flush the accumulated bytes into the store."""
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
        """List direct children of the remote directory."""
        if path not in self.dirs:
            raise FileNotFoundError(path)
        seen = set()
        for store in (self.files, self.dirs):
            for name in store:
                if name.startswith(path + "/"):
                    rel = name[len(path) + 1:]
                    if "/" not in rel:
                        seen.add(rel)
                    else:
                        seen.add(rel.split("/", 1)[0])
        return list(seen)

    def stat(self, path):
        """Return a minimal stat result for the remote path."""
        if path in self.dirs:
            from stat import S_IFDIR
            return self._stat(S_IFDIR, 0)
        if path in self.files:
            from stat import S_IFREG
            return self._stat(S_IFREG, len(self.files[path]))
        raise FileNotFoundError(path)

    def _stat(self, mode, size):
        """Build a minimal stat result with the given mode and size."""
        class _Stat:  # pylint: disable=too-few-public-methods
            """Stat double carrying mode and size."""
            st_mode = mode
            st_size = size
        return _Stat()

    def remove(self, path):
        """Remove the remote file from the store."""
        self.files.pop(path, None)


class _MockChannel:  # pylint: disable=too-few-public-methods
    """A channel double reporting a fixed exit code."""

    def __init__(self, exit_code=0):
        self._exit_code = exit_code

    def recv_exit_status(self):
        """Return the configured exit code."""
        return self._exit_code


class _MockStdout:  # pylint: disable=too-few-public-methods
    """An exec stdout double returning the fixture text."""

    def __init__(self, text, exit_code=0):
        self._text = text
        self.channel = _MockChannel(exit_code)

    def read(self):
        """Return the fixture text as bytes."""
        return self._text.encode("utf-8")


class _MockStderr:  # pylint: disable=too-few-public-methods
    """An exec stderr double returning the fixture text."""

    def __init__(self, text):
        self._text = text

    def read(self):
        """Return the fixture text as bytes."""
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

    def _make_job(self, uuid_full, status_value="prelude",  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
        """_execute_backend uploads the Snakefile and runs the wrapper."""
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
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)
        self.assertEqual(results["results"]["status"], "finished")
        job.set_status.assert_called_with("finished", "Remote execution completed")

    @patch("paramiko.SSHClient")
    def test_update_workflow_status_reports_finished_when_done_files_exist(self, mock_ssh_cls):
        """A done file on the runner yields a finished workflow status."""
        mock_ssh_cls.return_value = self.mock_client
        self.mock_sftp.dirs.add(self.workflow.remote_exec_path)
        short = "a" * 7
        self.mock_sftp.files[f"{self.workflow.remote_exec_path}/{short}.done"] = b""

        job = self._make_job("a" * 32)
        self.workflow.jobs = [job]
        os.makedirs(self.workflow.path, exist_ok=True)

        self.workflow.update_workflow_status()

        results_path = os.path.join(self.workflow.path, "results.json")
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)
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
        """download_outputs pulls remote stageout files into Storage."""
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
        """kill sends a signal to the remote pid from yuki.pid."""
        mock_ssh_cls.return_value = self.mock_client
        self.mock_sftp.files[f"{self.workflow.remote_exec_path}/yuki.pid"] = b"12345"

        def exec_side_effect(command, timeout=300):  # pylint: disable=unused-argument
            if command.startswith("cat"):
                return MagicMock(), _MockStdout("12345"), _MockStderr("")
            return MagicMock(), _MockStdout(""), _MockStderr("")

        self.mock_client.exec_command.side_effect = exec_side_effect

        self.workflow.kill()

        cmds = [call[0][0] for call in self.mock_client.exec_command.call_args_list]
        self.assertTrue(any("kill 12345" in cmd for cmd in cmds))

    @patch("paramiko.SSHClient")
    def test_ping_returns_true_when_remote_echo_succeeds(self, mock_ssh_cls):
        """ping returns True when the remote echo succeeds."""
        mock_ssh_cls.return_value = self.mock_client
        self.mock_client.exec_command.return_value = (
            MagicMock(), _MockStdout("ok"), _MockStderr("")
        )

        self.assertTrue(self.workflow.ping())

    @patch("paramiko.SSHClient")
    def test_ping_returns_false_on_connection_failure(self, mock_ssh_cls):
        """ping returns False when the connection is refused."""
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

        def exec_side_effect(command, timeout=300):  # pylint: disable=unused-argument
            if command.startswith("test -d"):
                return MagicMock(), _MockStdout("", exit_code=1), _MockStderr("")
            return MagicMock(), _MockStdout(""), _MockStderr("")

        self.mock_client.exec_command.side_effect = exec_side_effect

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

        cache_dir = (f"/tmp/yuki-workflows/impressions/"
                     f"{self.project_uuid}/{"a" * 32}")
        expected = f"{cache_dir}/data.root"
        self.assertIn(expected, self.mock_sftp.files)
        self.assertEqual(self.mock_sftp.files[expected], b"input-bytes")

    @patch("paramiko.SSHClient")
    def test_upload_files_remote_preserves_nested_input_structure(self, mock_ssh_cls):
        """Nested subdirectories inside input stageout must be uploaded recursively."""
        mock_ssh_cls.return_value = self.mock_client

        def exec_side_effect(command, timeout=300):  # pylint: disable=unused-argument
            if command.startswith("test -d"):
                return MagicMock(), _MockStdout("", exit_code=1), _MockStderr("")
            return MagicMock(), _MockStdout(""), _MockStderr("")

        self.mock_client.exec_command.side_effect = exec_side_effect

        job = self._make_job("a" * 32, is_input=True)
        job.machine_id = "runner-uuid"
        self.workflow.jobs = [job]

        src_stageout = os.path.join(
            self.tmpdir, ".Yuki", "Storage", self.project_uuid,
            "a" * 32, "runner-uuid", "stageout",
        )
        nested_dir = os.path.join(src_stageout, "data")
        os.makedirs(nested_dir, exist_ok=True)
        with open(os.path.join(nested_dir, "data.root"), "wb") as f:
            f.write(b"input-bytes")

        self._prepare_snakefile()
        self.workflow._upload_files_remote()

        cache_dir = (f"/tmp/yuki-workflows/impressions/"
                     f"{self.project_uuid}/{"a" * 32}")
        expected = f"{cache_dir}/data/data.root"
        self.assertIn(expected, self.mock_sftp.files)
        self.assertEqual(self.mock_sftp.files[expected], b"input-bytes")

    @patch("paramiko.SSHClient")
    def test_collect_remote_artifacts_preserves_nested_stageout(self, mock_ssh_cls):
        """Nested remote stageout files must be downloaded preserving structure."""
        mock_ssh_cls.return_value = self.mock_client

        impression = "i" * 32
        short = impression[:7]
        remote_stageout = f"{self.workflow.remote_exec_path}/imp{short}/stageout"
        self.mock_sftp.dirs.add(remote_stageout)
        self.mock_sftp.dirs.add(f"{remote_stageout}/plots")
        self.mock_sftp.files[f"{remote_stageout}/plots/mass.png"] = b"img"

        report = self.workflow._collect_remote_artifacts(
            impression, "stageout", "stageout.downloaded", "output"
        )

        local_stageout = os.path.join(
            self.tmpdir, ".Yuki", "Storage", self.project_uuid,
            impression, self.workflow.machine_id, "stageout"
        )
        self.assertTrue(os.path.exists(os.path.join(local_stageout, "plots", "mass.png")))
        self.assertIn("plots/mass.png", report["collected"])

    @patch("paramiko.SSHClient")
    def test_list_runner_files_remote_returns_relative_paths(self, mock_ssh_cls):
        """list_runner_files must return relative paths for nested files."""
        mock_ssh_cls.return_value = self.mock_client

        impression = "i" * 32
        short = impression[:7]
        remote_stageout = f"{self.workflow.remote_exec_path}/imp{short}/stageout"
        self.mock_sftp.dirs.add(remote_stageout)
        self.mock_sftp.dirs.add(f"{remote_stageout}/data")
        self.mock_sftp.files[f"{remote_stageout}/data/ntuple.root"] = b"data"

        out = self.workflow.list_runner_files(impression, "stageout")
        names = {f["name"] for f in out}
        self.assertIn("data/ntuple.root", names)

    @patch("paramiko.SSHClient")
    def test_download_selected_remote_matches_relative_path(self, mock_ssh_cls):
        """download_selected predicate must see relative paths for nested files."""
        mock_ssh_cls.return_value = self.mock_client

        impression = "i" * 32
        short = impression[:7]
        remote_stageout = f"{self.workflow.remote_exec_path}/imp{short}/stageout"
        self.mock_sftp.dirs.add(remote_stageout)
        self.mock_sftp.dirs.add(f"{remote_stageout}/plots")
        self.mock_sftp.files[f"{remote_stageout}/plots/mass.png"] = b"img"
        self.mock_sftp.files[f"{remote_stageout}/ntuple.root"] = b"data"

        from Yuki.kernel import file_types
        report = self.workflow.download_selected(
            impression, file_types.make_predicate("plots/*.png"), "stageout"
        )

        local_stageout = os.path.join(
            self.tmpdir, ".Yuki", "Storage", self.project_uuid,
            impression, self.workflow.machine_id, "stageout"
        )
        self.assertTrue(os.path.exists(os.path.join(local_stageout, "plots", "mass.png")))
        self.assertFalse(os.path.exists(os.path.join(local_stageout, "ntuple.root")))
        self.assertIn("plots/mass.png", report["collected"])


if __name__ == "__main__":
    unittest.main()
