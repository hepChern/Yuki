"""Impression storage management for Yuki kernel.

This module provides the ImpressionStorage class for managing workflow operations
and status tracking for individual impressions across different execution runners.
"""
import os
import json
from CelebiChrono.utils.metadata import ConfigFile
from . import file_types
from .vjob import VJob
from .vworkflow import VWorkflow
from .status_constants import CODA, FAILED, DISSONANCE, translate_to_musical

class ImpressionStorage:
    """Storage manager for impression workflow operations and status tracking."""
    def __init__(self, project_uuid, impression):
        # Imported lazily: Yuki.server.config pulls in the server package, whose
        # routes import this module — a module-level import here would create a
        # circular import (impression_storage -> server -> routes -> impression_storage).
        from ..server.config import config
        self.project_uuid = project_uuid
        self.impression = impression
        self.job_path = config.get_job_path(project_uuid, impression)

        # Load registry of runners
        config_file = config.get_config_file()
        self.runners = config_file.read_variable("runners", [])
        self.runners_id = config_file.read_variable("runners_id", {})

        # Metadata access
        self.job_config = ConfigFile(config.get_job_config_path(project_uuid, impression))

    def _get_runner_contexts(self):
        """Generator to yield active job/workflow pairs across all machines."""
        for machine in self.runners:
            machine_id = self.runners_id.get(machine)
            job = VJob(self.job_path, machine_id)

            if job.workflow_id():
                # Using the factory method from our previous refactor
                workflow = VWorkflow.create(self.project_uuid, [], job.workflow_id())
                yield machine, job, workflow

    def kill(self):
        """Kills all workflows associated with this storage entry."""
        for _, _, workflow in self._get_runner_contexts():
            workflow.kill()
        # Mark local record as failed
        VJob(self.job_path, None).set_status("failed")

    def collect(self):
        """Light default: plots + logs on success, logs on failure."""
        for name, job, workflow in self._get_runner_contexts():
            job_status = job.status(musical=True)
            if job_status == CODA:
                print(f"[{name}] Collecting plots + logs...")
                workflow.download_selected(self.impression, file_types.is_plot, "stageout")
                workflow.download_logs(self.impression)
            elif job_status in (FAILED, DISSONANCE):
                print(f"[{name}] Collecting logs...")
                workflow.download_logs(self.impression)

    def collect_files(self, kind, spec):
        """Download a subset of <kind> files matching a selection spec."""
        predicate = file_types.make_predicate(spec)
        for name, job, workflow in self._get_runner_contexts():
            if job.status(musical=True) == CODA:
                print(f"[{name}] Collecting {kind} matching {spec!r}...")
                workflow.download_selected(self.impression, predicate, kind)

    def file_status(self, kind="stageout"):
        """Merge runner listing with downloaded Storage state for <kind>.

        The runner listing of a finished job is immutable, so it is cached to
        <machine>/<kind>.filelist.json after the first successful live fetch and
        served from there afterwards — sparing a slow, sometimes flaky REANA
        list_files on every status. See _runner_files for the policy.
        """
        result = []
        for name, job, workflow in self._get_runner_contexts():
            machine_id = self.runners_id.get(name)
            machine_dir = os.path.join(self.job_path, machine_id)
            storage_dir = os.path.join(machine_dir, kind)
            downloaded = set(os.listdir(storage_dir)) if os.path.isdir(storage_dir) else set()

            runner_files = self._runner_files(job, workflow, kind, machine_dir)

            seen = set()
            for rf in runner_files:
                seen.add(rf["name"])
                result.append({
                    "name": rf["name"],
                    "size": rf.get("size", 0),
                    "type": file_types.classify(rf["name"]),
                    "in_runner": True,
                    "in_yuki": rf["name"] in downloaded,
                })
            for fn in sorted(downloaded - seen):
                full = os.path.join(storage_dir, fn)
                result.append({
                    "name": fn,
                    "size": os.path.getsize(full) if os.path.isfile(full) else 0,
                    "type": file_types.classify(fn),
                    "in_runner": False,
                    "in_yuki": True,
                })
        return result

    def _runner_files(self, job, workflow, kind, machine_dir):
        """Return the runner file listing for <kind>, served from a cache for a
        finished job to avoid a REANA round-trip on every status.

        Cached to <machine_dir>/<kind>.filelist.json keyed by the job's workflow
        id (a re-run invalidates it). The cache is only written once the job is
        finished and the live listing is non-empty, so a transient runner
        failure (empty result) is never persisted. A running job is always
        listed live, since its file set is still changing.
        """
        finished = job.status(musical=True) == CODA
        workflow_id = job.workflow_id()
        cache_path = os.path.join(machine_dir, kind + ".filelist.json")

        if finished and os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("workflow_id") == workflow_id:
                    return cached.get("files", [])
            except (OSError, ValueError):
                pass   # unreadable/corrupt cache -> fall through to a live fetch

        try:
            runner_files = workflow.list_runner_files(self.impression, kind)
        except Exception:
            runner_files = []

        if finished and runner_files:
            try:
                os.makedirs(machine_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump({"workflow_id": workflow_id, "files": runner_files}, fh)
            except OSError:
                pass   # best-effort cache; status still works without it

        return runner_files

    def collect_outputs(self):
        """Retrieves only output files from runners."""
        for name, job, workflow in self._get_runner_contexts():
            if job.status(musical=True) == CODA:
                print(f"[{name}] Collecting outputs...")
                workflow.download_outputs(self.impression)

    def collect_logs(self):
        """Retrieves only logs from runners."""
        for name, job, workflow in self._get_runner_contexts():
            job_status = job.status(musical=True)
            if job_status == CODA or job_status in (FAILED, DISSONANCE):
                print(f"[{name}] Collecting logs...")
                workflow.download_logs(self.impression)

        self.collect_engine_logs()

    def collect_engine_logs(self):
        """Retrieves engine logs from runners."""
        for name, job, workflow in self._get_runner_contexts():
            print(f"[{name}] Collecting engine logs...")
            workflow.get_workflow_logs()

    def watermark(self):
        """Applies watermarks to the stored results."""
        for name, job, workflow in self._get_runner_contexts():
            if job.status() == CODA:
                print(f"[{name}] Applying watermarks...")
                workflow.watermark(self.impression)

    def get_info(self):
        """Returns the location and ID of the first active runner."""
        for name, _, workflow in self._get_runner_contexts():
            return f"{name} {workflow.uuid}"
        return "UNDEFINED"
