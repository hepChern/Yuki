"""Impression storage management for Yuki kernel.

This module provides the ImpressionStorage class for managing workflow operations
and status tracking for individual impressions across different execution runners.
"""
import os
from CelebiChrono.utils.metadata import ConfigFile
from . import file_types
from .vjob import VJob
from .vworkflow import VWorkflow
from .status_constants import CODA, FAILED, DISSONANCE, translate_to_musical
from ..server.config import config

class ImpressionStorage:
    """Storage manager for impression workflow operations and status tracking."""
    def __init__(self, project_uuid, impression):
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
        """Merge runner listing with downloaded Storage state for <kind>."""
        result = []
        for name, _job, workflow in self._get_runner_contexts():
            machine_id = self.runners_id.get(name)
            storage_dir = os.path.join(self.job_path, machine_id, kind)
            downloaded = set(os.listdir(storage_dir)) if os.path.isdir(storage_dir) else set()
            try:
                runner_files = workflow.list_runner_files(self.impression, kind)
            except Exception:
                runner_files = []
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
