import os
from Chern.utils import utils
from Chern.utils import csys
from Chern.utils import metadata
from logging import getLogger

class VJob(object):
    """ Virtual class of the objects, including VVolume, VImage, VContainer
    """

    def __init__(self, path, machine_id):
        """ Initialize the project the only **information** of a object instance
        """
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
        """ Define the behavior of print(vobject)
        """
        return self.path

    def __repr__(self):
        """ Define the behavior of print(vobject)
        """
        return self.path

    def relative_path(self, path):
        """ Return a path relative to the path of this object
        """
        return os.path.relpath(path, self.path)

    def job_type(self):
        """ Return the type of the object under a specific path.
        If path is left blank, return the type of the object itself.
        """
        return self.config_file.read_variable("object_type", "")

    def object_type(self):
        """ Return the type of the object under a specific path.
        If path is left blank, return the type of the object itself.
        """
        return self.config_file.read_variable("object_type", "")

    def is_zombie(self):
        return self.job_type() == ""

    def set_runid(self, runid):
        self.run_config_file.write_variable("runid", runid)

    def runid(self):
        return self.run_config_file.read_variable("runid", "")

    def set_workflow_id(self, workflow_uuid):
        self.run_config_file.write_variable("workflow", workflow_uuid)

    def workflow_id(self):
        return self.run_config_file.read_variable("workflow", "")

    def environment(self):
        yaml_file = metadata.YamlFile(
            os.path.join(self.path, "contents", "chern.yaml")
            )
        return yaml_file.read_variable("environment", "")


    """ Let's consider when to update the status later
    """
    def status(self):
        logger = getLogger("YukiLogger")
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        logger.info(self.path)
        status = config_file.read_variable("status", "raw")
        if status != "raw":
            return status
        return "raw"

    def set_status(self, status):
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        config_file.write_variable("status", status)

    def update_data_status(self, status):
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        config_file.write_variable("status", status)

    def update_status(self, status):
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        if status == "PENDING":
            config_file.write_variable("status", "running")
        if status == "SUCCESS":
            config_file.write_variable("status", "success")

    def update_status_from_workflow(self, status):
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        current_status = config_file.read_variable("status", "raw")
        if current_status == "raw":
            config_file.write_variable("status", status)
        elif current_status == "running":
            if status == "success":
                config_file.write_variable("status", "success")
            elif status == "finished":
                config_file.write_variable("status", "finished")
            elif status == "failure":
                config_file.write_variable("status", "failure")
        elif current_status == "success" or current_status == "failure":
            pass
        else:
            config_file.write_variable("status", status)

    def error(self):
        if os.path.exists(self.path+"/error"):
            f = open(self.path+"/error")
            error = f.read()
            f.close()
            return error
        else:
            return ""

    def append_error(self, message):
        with open(self.path+"/error", "w") as f:
            f.write(message)
            f.write("\n")

    def dependencies(self):
        """ Return the preccessor of the object
        """
        return self.config_file.read_variable("dependencies", [])

    def files(self):
        file_list = []
        tree = self.config_file.read_variable("tree", [])
        for dirpath, dirnames, filenames in tree:
            for f in filenames:
                if f == "chern.yaml": continue
                name = self.short_uuid()
                if dirpath == ".":
                    name = os.path.join(name, f)
                else:
                    name = os.path.join(name, dirpath, f)
                file_list.append(name)
        return file_list

    def predecessors(self):
        dep = self.dependencies()
        path = os.path.join(os.environ["HOME"], ".Yuki", "Storage")
        return [VJob(os.path.join(path, x), self.machine_id) for x in dep]

    def impression(self):
        impression = self.path[-32:]
        return impression

    def short_uuid(self):
        return self.impression()[:7]
