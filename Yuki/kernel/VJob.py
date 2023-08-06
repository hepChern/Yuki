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
        self.path = path
        self.machine_id = machine_id
        self.run_path = os.path.join(self.path, machine_id, "run")
        self.config_file = metadata.ConfigFile(
            os.path.join(self.path, "config.json")
            )
        self.run_config_file = metadata.ConfigFile(
            os.path.join(self.path, machine_id, "config.json")
            )
        self.yaml_file = metadata.YamlFile(
            os.path.join(self.path, "contents", "chern.yaml")
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


    """ Let's consider when to update the status later
    """
    def status(self):
        logger = getLogger("YukiLogger")
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        logger.info(self.path)
        status = config_file.read_variable("status", "submitted")
        if status != "submitted":
            return status
        return "submitted"

    def update_status(self, status):
        config_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        if status == "PENDING":
            config_file.write_variable("status", "running")
        if status == "SUCCESS":
            config_file.write_variable("status", "success")

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
                name = self.impression()[:7]
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
        # [FIXME], the method ought to work for VJob, not VContainer
        impression = self.path[-32:]
        return impression
