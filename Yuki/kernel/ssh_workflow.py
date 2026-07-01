"""SSH/Remote workflow implementation.

This module provides the SshWorkflow class which implements workflow execution
by copying files to a remote host over SFTP and running Snakemake there over SSH.
"""
# pylint: disable=cyclic-import
import io
import json
import os
import stat
from logging import getLogger

from CelebiChrono.utils import metadata
from Yuki.utils.env_interpreter import EnvInterpreter
from .vworkflow import VWorkflow
from .status_constants import FAILED, DISSONANCE, translate_to_musical, is_terminal_status
from . import file_types

logger = getLogger("YukiLogger")

DEFAULT_ENVIRONMENT = "docker.io/reanahub/reana-env-root6:6.18.04"
DEFAULT_SSH_PORT = 22


class _SshConnection:
    """Thin wrapper around Paramiko for remote SSH/SFTP operations."""

    def __init__(self, host, user, key_path=None, port=DEFAULT_SSH_PORT):
        self.host = host
        self.user = user
        self.key_path = os.path.expanduser(key_path) if key_path else None
        self.port = port
        self._client = None
        self._sftp = None

    def connect(self):
        """Open SSH connection."""
        import paramiko

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": 30,
            "banner_timeout": 30,
        }
        if self.key_path and os.path.exists(self.key_path):
            connect_kwargs["key_filename"] = self.key_path
        self._client.connect(**connect_kwargs)
        self._sftp = self._client.open_sftp()

    def close(self):
        """Close SFTP and SSH connection."""
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def mkdir_p(self, remote_path, mode=0o755):
        """Recursively create remote directories."""
        if not remote_path or remote_path == "/":
            return
        try:
            self._sftp.stat(remote_path)
            return
        except FileNotFoundError:
            pass
        parent = os.path.dirname(remote_path)
        if parent and parent != remote_path:
            self.mkdir_p(parent, mode)
        self._sftp.mkdir(remote_path)
        self._sftp.chmod(remote_path, mode)

    def put(self, local_path, remote_path):
        """Upload a local file to the remote host."""
        self.mkdir_p(os.path.dirname(remote_path))
        self._sftp.put(local_path, remote_path)

    def put_text(self, text, remote_path, encoding="utf-8"):
        """Upload text content to a remote file."""
        self.mkdir_p(os.path.dirname(remote_path))
        with self._sftp.file(remote_path, "w") as remote_file:
            remote_file.write(text.encode(encoding) if isinstance(text, str) else text)

    def get(self, remote_path, local_path):
        """Download a remote file to the local host."""
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self._sftp.get(remote_path, local_path)

    def listdir(self, remote_path):
        """List entries in a remote directory.

        Returns a list of filenames, or an empty list if the directory
        does not exist.
        """
        try:
            return self._sftp.listdir(remote_path)
        except FileNotFoundError:
            return []

    def exists(self, remote_path):
        """Check whether a remote path exists."""
        try:
            self._sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def isfile(self, remote_path):
        """Check whether a remote path is a regular file."""
        try:
            return stat.S_ISREG(self._sftp.stat(remote_path).st_mode)
        except FileNotFoundError:
            return False

    def remove(self, remote_path):
        """Remove a remote file."""
        try:
            self._sftp.remove(remote_path)
        except FileNotFoundError:
            pass

    def exec(self, command, timeout=300):
        """Execute a command on the remote host.

        Returns (stdout_str, stderr_str, exit_code).
        """
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        stdin.close()
        return out, err, exit_code


class SshWorkflow(VWorkflow):
    """Remote/SSH implementation of VWorkflow.

    Behaves like NativeWorkflow but stages files and executes Snakemake on a
    remote host accessed over SSH/SFTP.
    """

    def __init__(self, project_uuid, jobs, uuid=None):
        """Initialize remote SSH workflow."""
        super().__init__(project_uuid, jobs, uuid)
        self.ssh_config = self._load_ssh_config()
        self.remote_exec_path = os.path.join(
            self.ssh_config.get("remote_workdir", "/tmp/yuki-workflows"),
            self.uuid,
        ).replace(os.sep, "/")

    def _load_ssh_config(self):
        """Read SSH connection settings from ~/.Yuki/config.json."""
        config_path = os.path.join(
            os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki")),
            "config.json"
        )
        config_file = metadata.ConfigFile(config_path)
        runner_id = self.machine_id or ""
        if not runner_id:
            return {}
        return {
            "host": config_file.read_variable("ssh_hosts", {}).get(runner_id, ""),
            "user": config_file.read_variable("ssh_users", {}).get(runner_id, ""),
            "key_path": config_file.read_variable("ssh_key_paths", {}).get(runner_id, ""),
            "port": config_file.read_variable("ssh_ports", {}).get(runner_id, DEFAULT_SSH_PORT),
            "remote_workdir": config_file.read_variable(
                "remote_workdirs", {}).get(runner_id, "/tmp/yuki-workflows"),
        }

    def _ssh(self):
        """Return a connected _SshConnection context manager."""
        cfg = self.ssh_config
        return _SshConnection(
            host=cfg.get("host", ""),
            user=cfg.get("user", ""),
            key_path=cfg.get("key_path"),
            port=cfg.get("port", DEFAULT_SSH_PORT),
        )

    def _execute_backend(self):
        """Execute workflow using a remote SSH backend."""
        try:
            self.logger("[SSH] Creating remote workflow structure")
            self._create_remote_structure()
        except Exception as e:
            self.logger(f"[SSH] Failed to create remote workflow structure: {e}")
            self.set_workflow_status("failed")
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                job.set_status(DISSONANCE, "SSH workflow construction failed")
            raise

        try:
            self.logger("[SSH] Uploading files")
            self._upload_files_remote()
        except Exception as e:
            self.logger(f"[SSH] Failed to upload files: {e}")
            self.set_workflow_status("failed")
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                job.set_status(DISSONANCE, "SSH workflow file upload failed")
            raise

        try:
            self.logger("[SSH] Starting remote Snakemake")
            self._start_remote_snakemake()
            self.set_workflow_status("running")
            self.logger(f"[SSH] Workflow running remotely in: {self.remote_exec_path}")
        except Exception as e:
            self.logger(f"[SSH] Failed to start remote Snakemake: {e}")
            self.set_workflow_status("failed")
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                job.set_status(DISSONANCE, "SSH workflow remote start failed")
            raise

    def _create_remote_structure(self):
        """Create remote workflow structure and write workflow_info.json."""
        workflow_info = {
            "workflow": {
                "uuid": self.uuid,
                "name": self.get_name(),
                "specification": {
                    "job_dependencies": self.dependencies,
                    "steps": self.steps,
                },
                "type": "snakemake",
                "file": "Snakefile"
            }
        }
        with self._ssh() as ssh:
            ssh.mkdir_p(self.remote_exec_path)
            remote_info_path = f"{self.remote_exec_path}/workflow_info.json"
            ssh.put_text(json.dumps(workflow_info, indent=2), remote_info_path)

    def _upload_files_remote(self):  # pylint: disable=too-many-locals
        """Upload all files to the remote execution directory.

        Unlike the native backend, the remote host has no FileStager to resolve
        a stage manifest, so input and rawdata files are copied directly into
        each job's ``imp<short>/stageout`` directory. Downstream jobs reach them
        through their ``gen -> ../imp<short>`` symlink (``gen/stageout/...``).
        """
        with self._ssh() as ssh:
            total_jobs = len(self.jobs)
            for j_idx, job in enumerate(self.jobs):
                files = job.files()
                total_files = len(files)
                for f_idx, name in enumerate(files):
                    src_path = os.path.join(job.path, "contents", name[8:])
                    dst_path = f"{self.remote_exec_path}/imp{name}"
                    if os.path.exists(src_path):
                        ssh.put(src_path, dst_path)
                        self.logger(
                            f"[SSH] [Job {j_idx+1}/{total_jobs}] Uploaded file "
                            f"{f_idx+1}/{total_files}: {name}"
                        )

                if job.environment() == "rawdata":
                    rawdata_path = os.path.join(job.path, "rawdata")
                    if os.path.exists(rawdata_path):
                        filelist = os.listdir(rawdata_path)
                        total_raw = len(filelist)
                        for f_idx, filename in enumerate(filelist):
                            src_path = os.path.join(rawdata_path, filename)
                            dst_path = (
                                f"{self.remote_exec_path}/"
                                f"imp{job.short_uuid()}/stageout/{filename}"
                            )
                            ssh.put(src_path, dst_path)
                            self.logger(
                                f"[SSH] [Job {j_idx+1}/{total_jobs}] Uploaded rawdata "
                                f"{f_idx+1}/{total_raw}: {filename}"
                            )

                elif job.is_input:
                    impression = job.path.split("/")[-1]
                    src_stageout = os.path.join(
                        os.environ["HOME"],
                        ".Yuki",
                        "Storage",
                        self.project_uuid,
                        impression,
                        job.machine_id,
                        "stageout"
                    )
                    if os.path.exists(src_stageout):
                        filelist = os.listdir(src_stageout)
                        total_input = len(filelist)
                        for f_idx, filename in enumerate(filelist):
                            src_path = os.path.join(src_stageout, filename)
                            dst_path = (
                                f"{self.remote_exec_path}/"
                                f"imp{job.short_uuid()}/stageout/{filename}"
                            )
                            ssh.put(src_path, dst_path)
                            self.logger(
                                f"[SSH] [Job {j_idx+1}/{total_jobs}] Uploaded input "
                                f"{f_idx+1}/{total_input}: {filename}"
                            )

            ssh.put(self.snakefile_path, f"{self.remote_exec_path}/Snakefile")
            self.logger("[SSH] Uploaded: Snakefile")

    def _start_remote_snakemake(self):
        """Upload a wrapper script and start Snakemake remotely in the background."""
        wrapper = '''#!/bin/bash
set -e
cd "$(dirname "$0")"
nohup snakemake --use-conda --cores all --snakefile Snakefile > snakemake.log 2>&1 &
echo $! > yuki.pid
wait $!
echo $? > yuki.exit
'''
        remote_wrapper = f"{self.remote_exec_path}/yuki_run.sh"
        with self._ssh() as ssh:
            ssh.put_text(wrapper, remote_wrapper)
            out, err, code = ssh.exec(f"chmod +x {remote_wrapper}")
            if code != 0:
                raise RuntimeError(
                    f"Failed to make yuki_run.sh executable: {err or out} (exit {code})"
                )
            out, err, code = ssh.exec(f"cd {self.remote_exec_path} && bash yuki_run.sh")
            if code != 0:
                detail = err.strip() if err.strip() else out.strip()
                raise RuntimeError(
                    f"Failed to start remote Snakemake: {detail} (exit {code})"
                )
            self.logger(f"[SSH] Remote Snakemake started")

    def _sync_external_job_status(self, job):
        """Poll remote status for an external dependency."""
        job.update_status_from_workflow(self.path, self.logger)

    def _read_remote_log_tail(self, ssh, short_uuid, max_chars=500):
        """Return tail of the highest-indexed celebi_user_step*.log on the remote host."""
        import re

        logs_dir = f"{self.remote_exec_path}/imp{short_uuid}/logs"
        try:
            entries = ssh.listdir(logs_dir)
        except FileNotFoundError:
            return ""

        pattern = re.compile(r"^celebi_user_step(\d+)\.log$")
        candidates = []
        for fname in entries:
            m = pattern.match(fname)
            if m:
                candidates.append((int(m.group(1)), fname))
        if not candidates:
            return ""

        candidates.sort(reverse=True)
        latest = candidates[0][1]
        remote_path = f"{logs_dir}/{latest}"
        out, _err, _code = ssh.exec(f"tail -c {max_chars} {remote_path}")
        return out

    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with remote markers."""
        with self._ssh() as ssh:
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                if is_terminal_status(translate_to_musical(job.status())):
                    continue

                short = job.short_uuid()
                done_path = f"{self.remote_exec_path}/{short}.done"
                if ssh.exists(done_path):
                    job.set_status("finished", "Remote execution completed")
                    continue

                if not workflow_terminal:
                    continue

                logs_dir = f"{self.remote_exec_path}/imp{short}/logs"
                has_logs = bool(ssh.listdir(logs_dir))
                if has_logs:
                    tail = self._read_remote_log_tail(ssh, short)
                    detail = f"Remote execution failed: {tail}" if tail else "Remote execution failed"
                    job.set_status(FAILED, detail)
                else:
                    job.set_status(
                        FAILED,
                        "Skipped: upstream dependency failed before this job ran",
                    )

    def update_workflow_status(self):
        """Update workflow status from remote execution."""
        try:
            all_done = True
            self.logger("[SSH] Checking jobs status...")

            # Derive the tracked jobs from self.jobs (loaded from local
            # config.json jobs_info when the workflow is reloaded). Do not rely
            # on workflow_info.json, which is only uploaded to the remote host.
            jobs = [
                job.short_uuid()
                for job in self.jobs
                if not job.is_input and job.job_type() != "algorithm"
            ]

            with self._ssh() as ssh:
                for job_uuid in jobs:
                    done_file = f"{self.remote_exec_path}/{job_uuid}.done"
                    if not ssh.exists(done_file):
                        all_done = False
                        break

            status = "finished" if all_done else "running"

            results = {
                "status": status,
                "progress": {
                    "total": len(jobs),
                    "completed": 0,
                }
            }

            if status == "finished":
                results["progress"]["completed"] = len(jobs)
            else:
                with self._ssh() as ssh:
                    results["progress"]["completed"] = sum(
                        1 for job_uuid in jobs
                        if ssh.exists(f"{self.remote_exec_path}/{job_uuid}.done")
                    )

            self.logger(
                f"[SSH] Workflow status: {status}, "
                f"Progress: {results['progress']['completed']}/{results['progress']['total']}"
            )

            path = os.path.join(self.path, "results.json")
            results_file = metadata.ConfigFile(path)
            results_file.write_variable("results", results)

            workflow_terminal = status in ("finished", "failed")
            self.propagate_job_statuses(workflow_terminal=workflow_terminal)

        except Exception as e:
            self.logger(f"[SSH] Failed to update workflow status: {e}")

    def check_status(self):
        """Check the status of remote workflow execution."""
        self.logger("[SSH] Checking status...")
        self.update_workflow_status()
        return self.status()

    def kill(self):
        """Kill remote workflow execution."""
        try:
            with self._ssh() as ssh:
                pid_file = f"{self.remote_exec_path}/yuki.pid"
                if ssh.exists(pid_file):
                    out, _err, _code = ssh.exec(f"cat {pid_file}")
                    pid = out.strip()
                    if pid:
                        ssh.exec(f"kill {pid}")
                        self.logger(f"[SSH] Sent SIGTERM to remote PID {pid}")
                else:
                    self.logger("[SSH] No PID file found; cannot kill remote process")
        except Exception as e:
            self.logger(f"[SSH] Error killing remote workflow: {e}")

        self.set_workflow_status("killed")
        for job in self.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            job.set_status(FAILED, "SSH workflow killed by user")

    def _collect_remote_artifacts(self, impression, artifact_dir, marker_name, label):
        """Collect a job artifact directory from remote execution into Storage."""
        src_path = f"{self.remote_exec_path}/imp{impression[0:7]}/{artifact_dir}"
        dst_path = os.path.join(
            os.environ["HOME"],
            ".Yuki",
            "Storage",
            self.project_uuid,
            impression,
            self.machine_id,
            artifact_dir
        )

        with self._ssh() as ssh:
            try:
                entries = ssh.listdir(src_path)
            except FileNotFoundError:
                self.logger(f"[SSH] No {label} found at: {src_path}")
                return False

            if not entries:
                return False

            os.makedirs(dst_path, exist_ok=True)
            total_files = len(entries)
            for i, filename in enumerate(entries):
                remote_file = f"{src_path}/{filename}"
                local_file = os.path.join(dst_path, filename)
                if os.path.exists(local_file):
                    continue
                if ssh.isfile(remote_file):
                    ssh.get(remote_file, local_file)
                    self.logger(f"[SSH] [{i+1}/{total_files}] Collected {label}: {filename}")

        marker_path = os.path.join(os.path.dirname(dst_path), marker_name)
        with open(marker_path, "w", encoding='utf-8') as _:
            pass
        return True

    def download(self, impression=None):
        """Download/collect results from remote execution."""
        self.logger("[SSH] Collecting results from remote execution")
        if impression:
            self._collect_remote_artifacts(
                impression, "stageout", "stageout.downloaded", "output"
            )
            self._collect_remote_artifacts(
                impression, "logs", "logs.downloaded", "log"
            )

    def download_outputs(self, impression=None):
        """Download outputs from remote execution."""
        if impression:
            self.logger("[SSH] Collecting outputs from remote execution")
            self._collect_remote_artifacts(
                impression, "stageout", "stageout.downloaded", "output"
            )

    def download_logs(self, impression=None):
        """Download logs from remote execution."""
        if impression:
            self.logger("[SSH] Collecting logs from remote execution")
            self._collect_remote_artifacts(
                impression, "logs", "logs.downloaded", "log"
            )

    def list_runner_files(self, impression, kind="stageout"):
        """List files in the remote execution dir under imp<short>/<kind>."""
        src_path = f"{self.remote_exec_path}/imp{impression[0:7]}/{kind}"
        result = []
        with self._ssh() as ssh:
            try:
                entries = ssh.listdir(src_path)
            except FileNotFoundError:
                return []
            for filename in entries:
                remote_file = f"{src_path}/{filename}"
                if ssh.isfile(remote_file):
                    size = self._sftp_file_size(ssh, remote_file)
                    result.append({"name": filename, "size": size})
        return result

    @staticmethod
    def _sftp_file_size(ssh, remote_path):
        """Return the size of a remote file via SFTP stat."""
        try:
            return ssh._sftp.stat(remote_path).st_size
        except Exception:
            return 0

    def download_selected(self, impression, predicate, kind="stageout"):
        """Copy only matching, not-yet-present files into Storage. No marker."""
        src_path = f"{self.remote_exec_path}/imp{impression[0:7]}/{kind}"
        dst_path = os.path.join(
            os.environ["HOME"], ".Yuki", "Storage",
            self.project_uuid, impression, self.machine_id, kind)
        os.makedirs(dst_path, exist_ok=True)

        with self._ssh() as ssh:
            try:
                entries = ssh.listdir(src_path)
            except FileNotFoundError:
                self.logger(f"[SSH] No {kind} found at: {src_path}")
                return
            for filename in entries:
                if not predicate(filename):
                    continue
                remote_file = f"{src_path}/{filename}"
                if not ssh.isfile(remote_file):
                    continue
                dst_file = os.path.join(dst_path, filename)
                if os.path.exists(dst_file):
                    continue
                ssh.get(remote_file, dst_file)
                self.logger(f"[SSH] Collected selected {kind}: {filename}")

    def get_workflow_logs(self):
        """Persist remote engine logs in the same workflow-level location as REANA."""
        logpath = os.path.join(self.path, "engine_logs.json")
        if os.path.exists(logpath):
            return

        engine_logs = {
            "backend": "ssh",
            "workflow_uuid": self.uuid,
            "remote_exec_path": self.remote_exec_path,
        }

        try:
            with self._ssh() as ssh:
                snakemake_log = f"{self.remote_exec_path}/snakemake.log"
                if ssh.exists(snakemake_log):
                    out, _err, _code = ssh.exec(f"cat {snakemake_log}")
                    engine_logs["snakemake_log"] = out
        except Exception as e:
            engine_logs["snakemake_log_error"] = str(e)

        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as f:
                engine_logs["workflow_log"] = f.read()

        results_path = os.path.join(self.path, "results.json")
        if os.path.exists(results_path):
            with open(results_path, "r", encoding="utf-8") as f:
                engine_logs["results"] = json.load(f)

        log_file = metadata.ConfigFile(logpath)
        log_file.write_variable("logs", engine_logs)

    def ping(self):
        """Ping remote SSH host."""
        try:
            with self._ssh() as ssh:
                out, err, code = ssh.exec("echo ok")
                if code == 0:
                    self.logger("[SSH] Remote host is reachable")
                    return True
                self.logger(f"[SSH] Remote host ping failed: {err}")
                return False
        except Exception as e:
            self.logger(f"[SSH] Remote host ping failed: {e}")
            return False

    def _write_environment_directive(self, snake_file, environment, indent=1):
        """Write a conda environment directive for remote SSH execution.

        Skips writing the directive for pure-copy procedures that do not need
        a conda environment.
        """
        if environment in ("rawdata", "datalist", "lhcb_ap_datalist", "script"):
            return
        if environment == DEFAULT_ENVIRONMENT:
            return
        conda_env = self._resolve_conda_environment(environment)
        snake_file.addline("conda:", indent)
        snake_file.addline(f'"{conda_env}"', indent + 1)

    def _resolve_conda_environment(self, environment):
        """Map a job environment string to a conda environment name."""
        if not environment:
            environment = DEFAULT_ENVIRONMENT

        config_path = os.path.join(
            os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki")),
            "config.json"
        )

        resolved = EnvInterpreter.resolve(environment, config_path)
        if resolved is not None:
            return resolved

        stripped = environment
        for prefix in ("docker://", "docker.io/", "docker:"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
                break

        if stripped != environment:
            resolved = EnvInterpreter.resolve(stripped, config_path)
            if resolved is not None:
                return resolved

        return stripped.replace("/", "_").replace(":", "_")
