"""Impression storage management for Yuki kernel.

This module provides the ImpressionStorage class for managing workflow operations
and status tracking for individual impressions across different execution runners.
"""
import datetime
import os
import json
from CelebiChrono.utils.metadata import ConfigFile
from . import file_types
from .vjob import VJob
from .vworkflow import VWorkflow
from .status_constants import CODA, FAILED, DISSONANCE, IN_MOVEMENT

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

    @staticmethod
    def _merge_reports(reports):
        """Merge a list of per-kind collect reports into one report."""
        merged = {"collected": [], "skipped": [], "failed": []}
        for report in reports:
            if not report:
                continue
            for key, value in report.items():
                if key in merged:
                    merged[key].extend(value)
        return merged

    def collect(self):
        """Light default: plots + logs on success, logs on failure."""
        report = {}
        for name, job, workflow in self._get_runner_contexts():
            job_status = job.status(musical=True)
            runner_report = {}
            if job_status == CODA:
                print(f"[{name}] Collecting plots + logs...")
                runner_report = self._merge_reports([
                    workflow.download_selected(self.impression, file_types.is_plot, "stageout"),
                    workflow.download_logs(self.impression),
                ])
            elif job_status in (FAILED, DISSONANCE):
                print(f"[{name}] Collecting logs...")
                runner_report = workflow.download_logs(self.impression)
            report[name] = runner_report
        return report

    def collect_files(self, kind, spec):
        """Download a subset of <kind> files matching a selection spec."""
        predicate = file_types.make_predicate(spec)
        report = {}
        for name, job, workflow in self._get_runner_contexts():
            if job.status(musical=True) == CODA:
                print(f"[{name}] Collecting {kind} matching {spec!r}...")
                report[name] = workflow.download_selected(self.impression, predicate, kind)
            else:
                report[name] = {"collected": [], "skipped": [], "failed": []}
        return report

    def file_status(self, kind="stageout"):  # pylint: disable=too-many-locals
        """Merge runner listing with downloaded Storage state for <kind>.

        The runner listing of a finished job is immutable, so it is cached to
        <machine>/<kind>.filelist.json after the first successful live fetch and
        served from there afterwards — sparing a slow, sometimes flaky REANA
        list_files on every status. See _runner_files for the policy.

        Remote-hosted data (registered via register-ssh-data) is listed from
        the host runner's managed impressions dir; see _remote_hosted_files.
        """
        result = self._remote_hosted_files(kind)
        for name, job, workflow in self._get_runner_contexts():
            machine_id = self.runners_id.get(name)
            machine_dir = os.path.join(self.job_path, machine_id)
            storage_dir = os.path.join(machine_dir, kind)
            downloaded = set()
            if os.path.isdir(storage_dir):
                for root, _dirs, files in os.walk(storage_dir):
                    for f in files:
                        downloaded.add(os.path.relpath(os.path.join(root, f), storage_dir))

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

    def _remote_hosted_files(self, kind):  # pylint: disable=too-many-locals,too-many-branches
        """List files of a remote-hosted data impression (register-ssh-data).

        Files live in the host runner's managed impressions dir. The listing
        is cached to <host_runner_id>/<kind>.filelist.json (same convention as
        _runner_files) and merged with the Storage state, so rows report
        in_runner/in_yuki like any other impression.
        """
        marker_path = os.path.join(self.job_path, "remote.json")
        if not os.path.exists(marker_path):
            return []
        marker = ConfigFile(marker_path)
        host_runner = marker.read_variable("host_runner_id", "")
        managed_path = marker.read_variable("remote_path", "")
        if not host_runner or not managed_path:
            return []

        machine_dir = os.path.join(self.job_path, host_runner)
        cache_path = os.path.join(machine_dir, kind + ".filelist.json")

        runner_files = None
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("workflow_id") == "remote-data":
                    runner_files = cached.get("files", [])
            except (OSError, ValueError):
                pass

        if runner_files is None:
            from . import remote_data_ops
            try:
                runner_files = remote_data_ops.list_managed_files(
                    host_runner, managed_path)
            except Exception:
                runner_files = []
            if runner_files:
                try:
                    os.makedirs(machine_dir, exist_ok=True)
                    with open(cache_path, "w", encoding="utf-8") as fh:
                        json.dump({"workflow_id": "remote-data",
                                   "files": runner_files}, fh)
                except OSError:
                    pass

        storage_dir = os.path.join(machine_dir, kind)
        downloaded = set()
        if os.path.isdir(storage_dir):
            for root, _dirs, files in os.walk(storage_dir):
                for f in files:
                    downloaded.add(os.path.relpath(
                        os.path.join(root, f), storage_dir))

        result = []
        for rf in runner_files:
            result.append({
                "name": rf["name"],
                "size": rf.get("size", 0),
                "type": file_types.classify(rf["name"]),
                "in_runner": True,
                "in_yuki": rf["name"] in downloaded,
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
        report = {}
        for name, job, workflow in self._get_runner_contexts():
            if job.status(musical=True) == CODA:
                print(f"[{name}] Collecting outputs...")
                report[name] = workflow.download_outputs(self.impression)
            else:
                report[name] = {"collected": [], "skipped": [], "failed": []}
        return report

    def collect_logs(self):
        """Retrieves only logs from runners.

        Logs are refreshed (existing local copies overwritten) whenever the
        job is executing or has reached a terminal state, so live log
        following sees growing remote logs.
        """
        report = {}
        for name, job, workflow in self._get_runner_contexts():
            job_status = job.status(musical=True)
            if job_status in (CODA, FAILED, DISSONANCE, IN_MOVEMENT):
                print(f"[{name}] Collecting logs...")
                report[name] = workflow.download_logs(self.impression, refresh=True)
            else:
                report[name] = {"collected": [], "skipped": [], "failed": []}

        self.collect_engine_logs()
        return report

    def collect_engine_logs(self):
        """Retrieves engine logs from runners."""
        for name, _job, workflow in self._get_runner_contexts():
            print(f"[{name}] Collecting engine logs...")
            workflow.get_workflow_logs()

    def watermark(self):
        """Applies watermarks to the stored results."""
        for name, job, workflow in self._get_runner_contexts():
            if job.status(musical=True) == CODA:
                print(f"[{name}] Applying watermarks...")
                workflow.watermark(self.impression)

    def get_info(self):
        """Returns the location and ID of the first active runner."""
        for name, _, workflow in self._get_runner_contexts():
            return f"{name} {workflow.uuid}"
        return "UNDEFINED"

    def update_distribution(self, overrides=None):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Refresh and persist the impression's distribution.json registry.

        The registry records, at impression granularity, where the data
        lives and how each copy got there:
        - produced_on: the runner that ran the producing workflow
        - 'yuki': origin 'collected' (union of local stageout dirs)
        - 'runner:<name>': a block with per-state entries:
          - 'workflow': data that only exists in the workflow's storage
            (origin 'produced'), recomputed on every refresh
          - 'cache': data in the runner's managed impressions cache
            (origin 'transferred' or 'registered')
        Transferred entries are written by the transfer task; they are
        preserved here and block recomputation of that state.
        overrides: {location: state_entry}; for 'yuki' it replaces the
        yuki entry, for a runner it replaces the 'cache' state (used by
        the transfer task to record the destination).
        """
        dist_path = os.path.join(self.job_path, "distribution.json")
        existing = {}
        if os.path.isfile(dist_path):
            try:
                with open(dist_path, encoding="utf-8") as fh:
                    existing = json.load(fh)
            except (OSError, ValueError):
                existing = {}

        # Keep only the states that must survive a refresh: transferred
        # yuki entries and transferred runner cache entries. Legacy flat
        # runner entries are migrated into state blocks.
        locations = {}
        for loc, value in existing.get("locations", {}).items():
            if "origin" in value:  # yuki entry or legacy flat runner entry
                if loc == "yuki":
                    if value.get("origin") == "transferred":
                        locations[loc] = value
                else:
                    state = "workflow" if value.get("origin") == "produced" \
                        else "cache"
                    locations[loc] = {state: value}
            elif loc != "yuki":
                cache = value.get("cache")
                if cache and cache.get("origin") == "transferred":
                    locations[loc] = {"cache": cache}
        produced_on = existing.get("produced_on")

        def make_entry(origin, files):
            return {
                "origin": origin,
                "files": len(files),
                "bytes": sum(f.get("size", 0) for f in files),
                "updated": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
            }

        for name, job, workflow in self._get_runner_contexts():
            if produced_on is None and job.workflow_id():
                produced_on = name
            machine_id = self.runners_id.get(name)
            machine_dir = os.path.join(self.job_path, machine_id)
            files = self._runner_files(job, workflow, "stageout", machine_dir)
            if files:
                locations.setdefault(f"runner:{name}", {})["workflow"] = \
                    make_entry("produced", files)

        # yuki: union of local stageout dirs across machines
        if "yuki" not in locations:
            yuki_files = []
            if os.path.isdir(self.job_path):
                for machine in sorted(os.listdir(self.job_path)):
                    root = os.path.join(self.job_path, machine, "stageout")
                    if not os.path.isdir(root):
                        continue
                    for dirpath, _dirs, filenames in os.walk(root):
                        for fname in filenames:
                            full = os.path.join(dirpath, fname)
                            yuki_files.append({
                                "name": os.path.relpath(full, root),
                                "size": os.path.getsize(full),
                            })
            if yuki_files:
                locations["yuki"] = make_entry("collected", yuki_files)

        # registered (remote-hosted data via register-ssh-data)
        marker = os.path.join(self.job_path, "remote.json")
        if os.path.exists(marker):
            marker_file = ConfigFile(marker)
            host_runner = marker_file.read_variable("host_runner_id", "")
            host_name = next(
                (n for n, mid in self.runners_id.items() if mid == host_runner),
                None)
            registered = [row for row in self._remote_hosted_files("stageout")
                          if row.get("in_runner")]
            key = f"runner:{host_name}" if host_name else None
            if registered and key:
                block = locations.setdefault(key, {})
                if "cache" not in block:
                    block["cache"] = make_entry("registered", registered)

        if overrides:
            for loc, value in overrides.items():
                if loc == "yuki":
                    locations[loc] = value
                else:
                    locations.setdefault(loc, {})["cache"] = value

        dist = {"produced_on": produced_on, "locations": locations}
        new_content = json.dumps(dist, indent=2)
        try:
            with open(dist_path, encoding="utf-8") as fh:
                if fh.read() == new_content:
                    return dist
        except OSError:
            pass
        os.makedirs(self.job_path, exist_ok=True)
        tmp_path = dist_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        os.replace(tmp_path, dist_path)
        return dist
