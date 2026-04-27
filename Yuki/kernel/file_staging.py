"""
File staging utility for local workflow execution.

Handles copying files from Storage to LocalWorkflows (stage in) and
copying results back (stage out) using hard links when possible.
"""
import os
import json
import shutil
from pathlib import Path


class FileStager:
    """Handle file staging with hard-link optimization."""

    def __init__(self, workflow_path, local_exec_path, project_uuid, logger=None):
        """
        Initialize file stager.

        Args:
            workflow_path: Path to ~/.Yuki/Workflows/<project>/<uuid>/
            local_exec_path: Path to ~/.Yuki/LocalWorkflows/<uuid>/
            project_uuid: Project UUID for accessing Storage
            logger: Optional logger function
        """
        self.workflow_path = workflow_path
        self.local_exec_path = local_exec_path
        self.project_uuid = project_uuid
        self.logger = logger
        self.yuki_home = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
        self.storage_dir = os.path.join(self.yuki_home, "Storage", project_uuid)

    def _log(self, message):
        """Log a message."""
        if self.logger:
            self.logger(message)

    def _copy_with_hardlink(self, src, dst):
        """
        Copy file using hard links if on same filesystem, else regular copy.

        Args:
            src: Source file path
            dst: Destination file path

        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure destination directory exists
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            # Check if src and dst are on same filesystem
            try:
                src_stat = os.stat(src)
                dst_stat = os.stat(os.path.dirname(dst))
                same_filesystem = src_stat.st_dev == dst_stat.st_dev
            except (OSError, AttributeError):
                same_filesystem = False

            # Try hard link if same filesystem
            if same_filesystem:
                try:
                    # Remove destination if it exists
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.link(src, dst)
                    return True
                except (OSError, PermissionError):
                    # Fall back to copy if hard link fails
                    pass

            # Fall back to regular copy
            shutil.copy2(src, dst)
            return True

        except Exception as e:
            self._log(f"[FILE_STAGING] Error copying {src} to {dst}: {e}")
            return False

    def _copy_directory_with_hardlinks(self, src_dir, dst_dir):
        """
        Copy directory tree using hard links where possible.

        Args:
            src_dir: Source directory
            dst_dir: Destination directory

        Returns:
            Number of files copied
        """
        count = 0
        try:
            for root, dirs, files in os.walk(src_dir):
                # Create corresponding directories in destination
                rel_root = os.path.relpath(root, src_dir)
                dst_root = os.path.join(dst_dir, rel_root) if rel_root != '.' else dst_dir
                os.makedirs(dst_root, exist_ok=True)

                # Copy files
                for file in files:
                    src_file = os.path.join(root, file)
                    dst_file = os.path.join(dst_root, file)
                    if self._copy_with_hardlink(src_file, dst_file):
                        count += 1

            return count
        except Exception as e:
            self._log(f"[FILE_STAGING] Error copying directory tree: {e}")
            return count

    def stage_in(self):
        """
        Stage input files from Storage to LocalWorkflows.

        Reads workflow_info.json to get job information, then copies all
        necessary files using hard links.

        Returns:
            True if successful, False otherwise
        """
        self._log("[FILE_STAGING] Starting stage-in...")

        try:
            # Read workflow_info.json to get job list
            workflow_info_path = os.path.join(self.local_exec_path, "workflow_info.json")
            if not os.path.exists(workflow_info_path):
                self._log("[FILE_STAGING] No workflow_info.json found")
                return False

            with open(workflow_info_path, 'r', encoding='utf-8') as f:
                workflow_info = json.load(f)

            steps = workflow_info.get("workflow", {}).get("specification", {}).get("steps", [])
            total_steps = len(steps)

            for step_idx, step in enumerate(steps):
                step_name = step.get("name", "")
                job_uuid = step_name[5:] if step_name.startswith("step_") else step_name

                self._log(f"[FILE_STAGING] [{step_idx + 1}/{total_steps}] Processing job: {job_uuid}")

                # Copy job files from Storage
                impression_dir = os.path.join(self.storage_dir, job_uuid)
                if os.path.isdir(impression_dir):
                    # Copy job definition files (contents)
                    contents_dir = os.path.join(impression_dir, "contents")
                    if os.path.isdir(contents_dir):
                        staging_dir = os.path.join(self.local_exec_path, f"imp{job_uuid[:7]}")
                        count = self._copy_directory_with_hardlinks(contents_dir, staging_dir)
                        self._log(f"[FILE_STAGING] Copied {count} job definition files")

                    # Copy rawdata if it exists
                    rawdata_dir = os.path.join(impression_dir, "rawdata")
                    if os.path.isdir(rawdata_dir):
                        staging_stageout = os.path.join(self.local_exec_path, f"imp{job_uuid[:7]}", "stageout")
                        count = self._copy_directory_with_hardlinks(rawdata_dir, staging_stageout)
                        self._log(f"[FILE_STAGING] Copied {count} rawdata files")

                    # Copy input files from previous job stageout
                    # Check all machines for this impression
                    machines_dir = os.path.join(impression_dir)
                    for machine_id in os.listdir(machines_dir):
                        machine_path = os.path.join(machines_dir, machine_id)
                        if not os.path.isdir(machine_path):
                            continue

                        stageout_dir = os.path.join(machine_path, "stageout")
                        if os.path.isdir(stageout_dir):
                            staging_stageout = os.path.join(self.local_exec_path, f"imp{job_uuid[:7]}", "stageout")
                            count = self._copy_directory_with_hardlinks(stageout_dir, staging_stageout)
                            if count > 0:
                                self._log(f"[FILE_STAGING] Copied {count} input files from {machine_id}")

            self._log("[FILE_STAGING] Stage-in completed")
            return True

        except Exception as e:
            self._log(f"[FILE_STAGING] Stage-in failed: {e}")
            return False

    def stage_out(self):
        """
        Copy execution results from LocalWorkflows back to Storage.

        Copies stageout and logs from execution directory to the job impressions
        in Storage.

        Returns:
            True if successful, False otherwise
        """
        self._log("[FILE_STAGING] Starting stage-out...")

        try:
            # Read workflow_info.json to get job list
            workflow_info_path = os.path.join(self.local_exec_path, "workflow_info.json")
            if not os.path.exists(workflow_info_path):
                self._log("[FILE_STAGING] No workflow_info.json found")
                return False

            with open(workflow_info_path, 'r', encoding='utf-8') as f:
                workflow_info = json.load(f)

            steps = workflow_info.get("workflow", {}).get("specification", {}).get("steps", [])
            total_steps = len(steps)

            for step_idx, step in enumerate(steps):
                step_name = step.get("name", "")
                job_uuid = step_name[5:] if step_name.startswith("step_") else step_name

                self._log(f"[FILE_STAGING] [{step_idx + 1}/{total_steps}] Collecting results for: {job_uuid}")

                # Get the execution directory for this job
                exec_job_dir = os.path.join(self.local_exec_path, f"imp{job_uuid[:7]}")

                # Copy stageout files back to Storage
                src_stageout = os.path.join(exec_job_dir, "stageout")
                if os.path.isdir(src_stageout):
                    # Determine machine_id (use the one from storage if it exists)
                    impression_dir = os.path.join(self.storage_dir, job_uuid)
                    machine_id = None

                    # Try to find an existing machine_id in the impression
                    if os.path.isdir(impression_dir):
                        for entry in os.listdir(impression_dir):
                            entry_path = os.path.join(impression_dir, entry)
                            if os.path.isdir(entry_path) and entry != "contents" and entry != "rawdata":
                                machine_id = entry
                                break

                    if not machine_id:
                        # Use the machine_id from workflow config
                        config_path = os.path.join(self.workflow_path, "config.json")
                        if os.path.exists(config_path):
                            try:
                                with open(config_path, 'r') as f:
                                    config = json.load(f)
                                    machine_id = config.get("machine_id", "default")
                            except:
                                machine_id = "default"
                        else:
                            machine_id = "default"

                    # Copy stageout files
                    dst_stageout = os.path.join(impression_dir, machine_id, "stageout")
                    count = self._copy_directory_with_hardlinks(src_stageout, dst_stageout)
                    self._log(f"[FILE_STAGING] Copied {count} output files")

                    # Create downloaded marker
                    downloaded_marker = os.path.join(impression_dir, machine_id, "stageout.downloaded")
                    os.makedirs(os.path.dirname(downloaded_marker), exist_ok=True)
                    with open(downloaded_marker, 'w', encoding='utf-8') as f:
                        pass

                # Copy logs back to Storage
                src_logs = os.path.join(exec_job_dir, "logs")
                if os.path.isdir(src_logs):
                    dst_logs = os.path.join(impression_dir, machine_id, "logs")
                    count = self._copy_directory_with_hardlinks(src_logs, dst_logs)
                    self._log(f"[FILE_STAGING] Copied {count} log files")

                    # Create logs downloaded marker
                    logs_marker = os.path.join(impression_dir, machine_id, "logs.downloaded")
                    with open(logs_marker, 'w', encoding='utf-8') as f:
                        pass

            self._log("[FILE_STAGING] Stage-out completed")
            return True

        except Exception as e:
            self._log(f"[FILE_STAGING] Stage-out failed: {e}")
            return False
