"""
Native/Local workflow implementation.

This module provides the NativeWorkflow class which implements workflow execution
by copying files to a local directory for manual/local execution instead of
submitting to a remote REANA server.
"""
# pylint: disable=cyclic-import
import os
import shutil
import json
from CelebiChrono.utils import metadata
from Yuki.utils.env_interpreter import EnvInterpreter
from .vworkflow import VWorkflow
from .status_constants import FAILED, DISSONANCE, CODA, translate_to_musical, is_terminal_status

DEFAULT_ENVIRONMENT = "docker.io/reanahub/reana-env-root6:6.18.04"


class NativeWorkflow(VWorkflow):
    """Local/Native implementation of VWorkflow."""

    def __init__(self, project_uuid, jobs, uuid=None):
        """Initialize local workflow."""
        super().__init__(project_uuid, jobs, uuid)
        # Create a local execution directory
        self.local_exec_path = os.path.join(
                os.path.join(
                    os.environ["HOME"],
                    ".Yuki",
                    "LocalWorkflows",
                    self.uuid,
                    )
                )
        os.makedirs(self.local_exec_path, exist_ok=True)

    def _execute_backend(self):
        """Execute workflow using local backend (copy files locally)."""
        try:
            self.logger("[LOCAL] Creating workflow structure")
            self.create_local_structure()
        except Exception as e:
            self.logger(f"[LOCAL] Failed to create workflow structure: {e}")
            self.set_workflow_status("failed")
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                job.set_status(DISSONANCE, "Native workflow construction failed")
            raise

        try:
            self.logger("[LOCAL] Copying files")
            self.copy_files_local()
        except Exception as e:
            self.logger(f"[LOCAL] Failed to copy files: {e}")
            self.set_workflow_status("failed")
            for job in self.jobs:
                if job.is_input:
                    continue
                if job.job_type() == "algorithm":
                    continue
                job.set_status(DISSONANCE, "Native workflow file copy failed")
            raise

        # Set status to ready for local execution
        self.set_workflow_status("ready_for_local_execution")
        self.logger(f"[LOCAL] Workflow prepared in: {self.local_exec_path}")
        self.logger(f"[LOCAL] Snakefile: {os.path.join(self.local_exec_path, 'Snakefile')}")
        self.logger("[LOCAL] You can now run: snakemake --use-conda --cores all")

    def _sync_external_job_status(self, job):
        """Poll local status for external dependency."""
        # In local mode, check if files exist
        job.update_status_from_workflow(self.path, self.logger)

    def create_local_structure(self):
        """Create local workflow structure."""
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

        # Write workflow info
        self.logger(f"[LOCAL] Writing workflow info to "
                     f"{os.path.join(self.local_exec_path, 'workflow_info.json')}")
        with open(os.path.join(self.local_exec_path, "workflow_info.json"),
                  "w", encoding='utf-8') as f:
            json.dump(workflow_info, f, indent=2)

    def copy_files_local(self):  # pylint: disable=too-many-locals
        """Copy all files to local execution directory."""
        stage_manifest = []

        total_jobs = len(self.jobs)
        for j_idx, job in enumerate(self.jobs):
            # Copy job files (these are small metadata files - safe to copy eagerly)
            files = job.files()
            total_files = len(files)
            for f_idx, name in enumerate(files):
                # Data files under stageout/ are deferred to the manifest
                # so that symlink/CoW-clone targets resolve on the host
                if "/stageout/" in name:
                    continue
                src_path = os.path.join(job.path, "contents", name[8:])
                dst_path = os.path.join(self.local_exec_path, "imp" + name)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.exists(src_path):
                    shutil.copy2(src_path, dst_path)
                    self.logger(f"[LOCAL] [Job {j_idx+1}/{total_jobs}] Copied file "
                                 f"{f_idx+1}/{total_files}: {name}")

            # Record rawdata files for lazy staging (links resolved at run-workflow time)
            if job.environment() == "rawdata":
                rawdata_path = os.path.join(job.path, "rawdata")
                if os.path.exists(rawdata_path):
                    filelist = os.listdir(rawdata_path)
                    total_raw = len(filelist)
                    for f_idx, filename in enumerate(filelist):
                        src_path = os.path.join(rawdata_path, filename)
                        dst_rel = os.path.join(
                            f"imp{job.short_uuid()}",
                            "stageout",
                            filename
                        )
                        stage_manifest.append({
                            "type": "rawdata",
                            "job_uuid": job.uuid,
                            "src_path": src_path,
                            "dst_rel": dst_rel,
                        })
                        self.logger(f"[LOCAL] [Job {j_idx+1}/{total_jobs}] Queued rawdata "
                                     f"{f_idx+1}/{total_raw}: {filename}")

            # Record input files for lazy staging (links resolved at run-workflow time)
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
                        dst_rel = os.path.join(
                            f"imp{job.short_uuid()}",
                            "stageout",
                            filename
                        )
                        stage_manifest.append({
                            "type": "input",
                            "job_uuid": job.uuid,
                            "machine_id": job.machine_id,
                            "src_path": src_path,
                            "dst_rel": dst_rel,
                        })
                        self.logger(f"[LOCAL] [Job {j_idx+1}/{total_jobs}] Queued input "
                                     f"{f_idx+1}/{total_input}: {filename}")

        # Write stage manifest for FileStager to process on the host
        if stage_manifest:
            manifest_path = os.path.join(self.local_exec_path, "stage_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"entries": stage_manifest}, f, indent=2)
            self.logger(f"[LOCAL] Stage manifest written: {len(stage_manifest)} entries")

        # Copy Snakefile
        shutil.copy2(
            self.snakefile_path,
            os.path.join(self.local_exec_path, "Snakefile")
        )
        self.logger("[LOCAL] Copied: Snakefile")

    def _write_environment_directive(self, snake_file, environment, indent=1):
        """Write a conda environment directive for local native execution.

        Skips writing the directive for pure-copy procedures (setup, finalize,
        rawdata, datalist, script) that do not need a conda environment.
        """
        if environment in ("rawdata", "datalist", "script"):
            return
        if environment == DEFAULT_ENVIRONMENT:
            return
        conda_env = self._resolve_conda_environment(environment)
        snake_file.addline("conda:", indent)
        snake_file.addline(f'"{conda_env}"', indent + 1)

    def _resolve_conda_environment(self, environment):
        """Map a job environment string to a conda environment name.

        Resolution order:
        1. ``conda_env_map`` from ~/.Yuki/config.json (structured or plain)
        2. Strip common Docker prefixes and try lookup again
        3. Sanitise the image name for use as a conda env name
        """
        if not environment:
            environment = DEFAULT_ENVIRONMENT

        config_path = os.path.join(
            os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki")),
            "config.json"
        )

        # 1. Exact match
        resolved = EnvInterpreter.resolve(environment, config_path)
        if resolved is not None:
            return resolved

        # 2. Strip Docker prefixes and try again
        stripped = environment
        for prefix in ("docker://", "docker.io/", "docker:"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
                break

        if stripped != environment:
            resolved = EnvInterpreter.resolve(stripped, config_path)
            if resolved is not None:
                return resolved

        # 3. Fallback: sanitise for use as conda env name
        return stripped.replace("/", "_").replace(":", "_")

    def propagate_job_statuses(self, workflow_terminal=False):
        """Reconcile each VJob's status.json with on-disk markers."""

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

    def update_workflow_status(self):
        """Update workflow status from local execution."""
        try:
            # Check if all output files exist
            all_done = True
            self.logger("[LOCAL] Checking jobs status...")

            # Get jobs from json
            workflow_info_json = os.path.join(self.local_exec_path, "workflow_info.json")
            with open(workflow_info_json, "r", encoding='utf-8') as f:
                workflow_info = json.load(f)
            jobs = []
            for step in workflow_info["workflow"]["specification"]["steps"]:
                name = step["name"]
                jobs.append(name[4:])

            for job_uuid in jobs:
                done_file = os.path.join(
                    self.local_exec_path,
                    f"{job_uuid}.done"
                    )

                if not os.path.exists(done_file):
                    all_done = False
                    break

            if all_done:
                status = "finished"
            else:
                # Check if workflow is running
                status = "running"

            results = {
                "status": status,
                "progress": {
                    "total": len(jobs),
                    "completed": sum(
                        1 for job_uuid in jobs
                        if os.path.exists(
                            os.path.join(
                                self.local_exec_path,
                                f"{job_uuid}.done"
                            )
                        )
                    )
                }
            }
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

    def check_status(self):
        """Check the status of local workflow execution."""
        self.logger("[LOCAL] Checking status...")
        self.update_workflow_status()
        return self.status()

    def kill(self):
        """Kill local workflow execution."""
        self.logger("[LOCAL] Killing local workflow (manual intervention required)")
        self.set_workflow_status("killed")
        for job in self.jobs:
            if job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            job.set_status(FAILED, "Native workflow killed by user")

    def _collect_artifacts(self, impression, artifact_dir, marker_name, label):
        """Collect a job artifact directory from local execution into Storage.

        Returns True when the artifact directory existed and was collected.
        """
        src_path = os.path.join(
            self.local_exec_path,
            f"imp{impression[0:7]}",
            artifact_dir
        )
        dst_path = os.path.join(
            os.environ["HOME"],
            ".Yuki",
            "Storage",
            self.project_uuid,
            impression,
            self.machine_id,
            artifact_dir
        )

        if not os.path.exists(src_path):
            self.logger(f"[LOCAL] No {label} found at: {src_path}")
            return False

        os.makedirs(dst_path, exist_ok=True)
        filelist = os.listdir(src_path)
        total_files = len(filelist)
        for i, filename in enumerate(filelist):
            src_file = os.path.join(src_path, filename)
            dst_file = os.path.join(dst_path, filename)
            shutil.copy2(src_file, dst_file)
            self.logger(f"[LOCAL] [{i+1}/{total_files}] Collected {label}: {filename}")

        marker_path = os.path.join(os.path.dirname(dst_path), marker_name)
        with open(marker_path, "w", encoding='utf-8') as _:
            pass
        return True

    def download(self, impression=None):
        """Download/collect results from local execution."""
        self.logger("[LOCAL] Collecting results from local execution")
        if impression:
            self._collect_artifacts(
                impression, "stageout", "stageout.downloaded", "output"
            )
            self._collect_artifacts(
                impression, "logs", "logs.downloaded", "log"
            )

    def download_outputs(self, impression=None):
        """Download outputs from local execution."""
        if impression:
            self.logger("[LOCAL] Collecting outputs from local execution")
            self._collect_artifacts(
                impression, "stageout", "stageout.downloaded", "output"
            )

    def download_logs(self, impression=None):
        """Download logs from local execution."""
        if impression:
            self.logger("[LOCAL] Collecting logs from local execution")
            self._collect_artifacts(
                impression, "logs", "logs.downloaded", "log"
            )

    def get_workflow_logs(self):
        """Persist local engine logs in the same workflow-level location as REANA."""
        logpath = os.path.join(self.path, "engine_logs.json")
        if os.path.exists(logpath):
            return

        engine_logs = {
            "backend": "native",
            "workflow_uuid": self.uuid,
            "local_exec_path": self.local_exec_path,
        }

        snakemake_log_path = os.path.join(self.local_exec_path, "snakemake.log")
        if os.path.exists(snakemake_log_path):
            with open(snakemake_log_path, "r", encoding="utf-8") as f:
                engine_logs["snakemake_log"] = f.read()

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
        """Ping local system."""
        self.logger("[LOCAL] Local workflow system is available")
        return True
