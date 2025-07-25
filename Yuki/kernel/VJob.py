"""
Virtual Job module for Yuki kernel.

This module contains the VJob class which represents a virtual job object
that can include VVolume, VImage, VContainer and other related entities.
"""
import os

from Chern.utils import metadata

class VJob:
    """Virtual class of the objects, including VVolume, VImage, VContainer."""

    def __init__(self, path, machine_id):
        """Initialize the project with the only **information** of an object instance."""
        self.is_input = False
        self.path = path
        self.uuid = path[-32:]
        self.machine_id = machine_id
        self.config_file = metadata.ConfigFile(
            os.path.join(self.path, "config.json")
            )
        self.yaml_file = metadata.YamlFile(
            os.path.join(self.path, "contents", "chern.yaml")
        )
        if self.environment() == "rawdata":
            self.is_input = True
        if machine_id is not None:
            self.run_path = os.path.join(self.path, machine_id, "run")
            self.run_config_file = metadata.ConfigFile(
                os.path.join(self.path, machine_id, "config.json")
                )


    def __str__(self):
        """Define the behavior of print(vobject)."""
        return self.path

    def __repr__(self):
        """Define the behavior of print(vobject)."""
        return self.path

    def relative_path(self, path):
        """Return a path relative to the path of this object."""
        return os.path.relpath(path, self.path)

    def job_type(self):
        """Return the type of the object under a specific path."""
        print("job_type", self.config_file.read_variable("object_type", ""))
        return self.config_file.read_variable("object_type", "")

    def object_type(self):
        """Return the type of the object under a specific path."""
        return self.config_file.read_variable("object_type", "")

    def is_zombie(self):
        """Check if this job is a zombie (empty job type)."""
        return self.job_type() == ""

    def set_runid(self, runid):
        """Set the run ID for this job."""
        self.run_config_file.write_variable("runid", runid)

    def runid(self):
        """Get the run ID for this job."""
        return self.run_config_file.read_variable("runid", "")

    def set_workflow_id(self, workflow_uuid):
        """Set the workflow ID for this job."""
        self.run_config_file.write_variable("workflow", workflow_uuid)

    def workflow_id(self):
        """Get the workflow ID for this job."""
        return self.run_config_file.read_variable("workflow", "")

    def environment(self):
        """Get the environment type from the YAML configuration."""
        yaml_file = metadata.YamlFile(
            os.path.join(self.path, "contents", "chern.yaml")
            )
        return yaml_file.read_variable("environment", "")


    def status(self):
        """Get the current status of the job."""
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        status = config_file.read_variable("status", "raw")
        if status != "raw":
            return status
        return "raw"

    def set_status(self, status):
        """Set the status of the job."""
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        config_file.write_variable("status", status)

    def update_data_status(self, status):
        """Update the data status of the job."""
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        config_file.write_variable("status", status)

    def update_status(self, status):
        """Update the status based on workflow status."""
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        if status == "PENDING":
            config_file.write_variable("status", "running")
        if status == "SUCCESS":
            config_file.write_variable("status", "success")

    def update_status_from_workflow(self, status):
        """Update job status based on workflow status."""
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        current_status = config_file.read_variable("status", "raw")
        if current_status == "raw":
            config_file.write_variable("status", status)
        elif current_status == "running":
            if status == "success":
                config_file.write_variable("status", "success")
            elif status == "finished":
                config_file.write_variable("status", "finished")
            elif status == "failed":
                config_file.write_variable("status", "failed")
        elif current_status in ('success', 'failed'):
            pass
        else:
            config_file.write_variable("status", status)

    def error(self):
        """Get error message if any."""
        error_path = self.path + "/error"
        if os.path.exists(error_path):
            with open(error_path, encoding='utf-8') as f:
                return f.read()
        return ""

    def append_error(self, message):
        """Append an error message to the error file."""
        with open(self.path + "/error", "w", encoding='utf-8') as f:
            f.write(message)
            f.write("\n")

    def dependencies(self):
        """Return the predecessor of the object."""
        return self.config_file.read_variable("dependencies", [])

    def files(self):
        """Get list of files in this job."""
        file_list = []
        tree = self.config_file.read_variable("tree", [])
        for dirpath, _, filenames in tree:
            for f in filenames:
                if f == "chern.yaml":
                    continue
                name = f"{self.short_uuid()}"
                if dirpath == ".":
                    name = os.path.join(name, f)
                else:
                    name = os.path.join(name, dirpath, f)
                file_list.append(name)
        return file_list

    def predecessors(self):
        """Get predecessor VJob objects."""
        dep = self.dependencies()
        path = os.path.join(os.environ["HOME"], ".Yuki", "Storage")
        return [VJob(os.path.join(path, x), self.machine_id) for x in dep]

    def impression(self):
        """Get the impression UUID."""
        impression = self.path[-32:]
        return impression

    def short_uuid(self):
        """Get the short UUID (first 7 characters)."""
        return self.impression()[:7]
