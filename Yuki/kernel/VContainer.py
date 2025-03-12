"""

"""
import subprocess
import Chern
import os
import sys
import shutil
from Chern.utils import utils
from Chern.utils import csys
from Chern.utils import metadata
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VImage import VImage
from time import sleep

class VContainer(VJob):
    """
    A VContainer should manage the physical container.
    The VContainer should be able to interact with the, or a
    A container should be able to be created from a task?
    What to determine a container?
    """
    def __init__(self, path, machine_id):
        super(VContainer, self).__init__(path, machine_id)

    def run(self):
        sleep(1)
        # workflow = VWorkflow(self)
        # response = workflow.run()
        # return response
        return ""

    def machine_storage(self):
        config_file = metadata.ConfigFile(os.path.join(os.environ["HOME"], ".Yuki/config.json"))
        machine_id = config_file.read_variable("machine_id")
        return "run." + machine_id

    def satisfied(self):
        for pred_object in self.dependencies():
            if pred_object.is_zombie():
                return False
            if pred_object.job_type() == "task" and VContainer(pred_object.path).status() != "done":
                return False
            if pred_object.job_type() == "algorithm" and VImage(pred_object.path).status() != "built":
                return False
        return True


    def inputs(self):
        """
        Input data.
        """
        alias_to_imp = self.config_file.read_variable("alias_to_impression", {})
        print(alias_to_imp)
        return (alias_to_imp.keys(), alias_to_imp)

    def image(self):
        predecessors = self.predecessors()
        for pred_job in predecessors:
            if pred_job.job_type() == "algorithm":
                return VImage(pred_job.path, self.machine_id)
        return None

    def step(self):
        commands = ["mkdir -p {}".format(self.short_uuid())]
        commands.append("cd {}".format(self.short_uuid()))
        if self.is_input:
            raw_commands = []
        else:
            raw_commands = self.image().yaml_file.read_variable("commands", [])
        for command in raw_commands:
            # Replace the commands (parameters):
            parameters, values = self.parameters()
            for parameter in parameters:
                value = values[parameter]
                name = "${"+ parameter +"}"
                command = command.replace(name, value)

            # Replace the commands (inputs):
            alias_list, alias_map = self.inputs()
            for alias in alias_list:
                impression = alias_map[alias]
                name = "${"+ alias +"}"
                command = command.replace(name, impression[:7])
            command = command.replace("${workspace}", "$REANA_WORKSPACE")
            command = command.replace("${output}", self.short_uuid())
            image = self.image()
            if image:
                command = command.replace("${code}", image.short_uuid())
            commands.append(command.replace("\"", "\\\""))
        step = {}
        step["commands"] = commands
        commands.append("cd $REANA_WORKSPACE")
        commands.append("touch {}.done".format(self.short_uuid()))
        commands = " && ".join(commands)
        if self.is_input:
            step["environment"] = "reanahub/reana-env-root6:6.18.04"
        else:
            step["environment"] = self.environment()
        step["kubernetes_memory_limit"] = self.memory()
        step["name"] = "step{}".format(self.short_uuid())
        step["kubernetes_uid"] = None
        step["compute_backend"] = None

        return step

    def snakemake_rule(self):
        commands = ["mkdir -p {}".format(self.short_uuid())]
        commands.append("cd {}".format(self.short_uuid()))
        if self.is_input:
            raw_commands = []
        else:
            raw_commands = self.image().yaml_file.read_variable("commands", [])
        for command in raw_commands:
            # Replace the commands (parameters):
            parameters, values = self.parameters()
            for parameter in parameters:
                value = values[parameter]
                name = "${"+ parameter +"}"
                command = command.replace(name, value)

            # Replace the commands (inputs):
            alias_list, alias_map = self.inputs()
            for alias in alias_list:
                impression = alias_map[alias]
                name = "${"+ alias +"}"
                command = command.replace(name, impression[:7])
            command = command.replace("${workspace}", "$REANA_WORKSPACE")
            command = command.replace("${output}", self.short_uuid())
            image = self.image()
            if image:
                command = command.replace("${code}", image.short_uuid())
            commands.append(command.replace("\"", "\\\""))
        step = {}
        step["commands"] = commands
        commands.append("cd $REANA_WORKSPACE")
        commands.append("touch {}.done".format(self.short_uuid()))
        if self.is_input:
            step["environment"] = "reanahub/reana-env-root6:6.18.04"
        else:
            step["environment"] = self.environment()
        step["memory"] = self.memory()
        step["name"] = "step{}".format(self.short_uuid())
        step["output"] = "{}.done".format(self.short_uuid())

        step["inputs"] = []
        if not self.is_input:
            alias_list, alias_map = self.inputs()
            for alias in alias_list:
                impression = alias_map[alias]
                step["inputs"].append("{}.done".format(impression[:7]))
            image = self.image()
            if image:
                step["inputs"].append("{}.done".format(image.short_uuid()))

        return step


    def environment(self):
        return self.yaml_file.read_variable("environment", "")

    def memory(self):
        return self.yaml_file.read_variable("kubernetes_memory_limit", "")


    def create_container(self, container_type="task"):
        mounts = "-v {1}:/data/{0}".format(self.impression(), os.path.join(self.path, self.machine_storage(), "output"))
        for input_container in self.inputs():
            mounts += " -v {1}:/data/{0}:ro".format(input_container.impression(),
                                                  os.path.join(input_container.path, input_container.storage(), "output"))
        image_id = self.image().image_id()
        ps = subprocess.Popen("docker create {0} {1}".format(mounts, image_id),
                              shell=True, stdout=subprocess.PIPE)
        print("docker create {0} {1}".format(mounts, image_id),
                              file=sys.stderr)

        ps.wait()
        container_id = ps.stdout.read().decode().strip()

        run_path = os.path.join(self.path, self.machine_storage())
        config_file = metadata.ConfigFile(os.path.join(run_path, "status.json"))
        config_file.write_variable("container_id", container_id)

    def copy_arguments_file(self):
        arguments_file = os.path.join(self.path, self.machine_storage(), "arguments")
        ps = subprocess.Popen("docker cp {0} {1}:/root".format(arguments_file, self.container_id())
                              , shell=True)
        ps.wait()

    def parameters(self):
        """
        Read the parameters file
        """
        parameters = self.yaml_file.read_variable("parameters", {})
        return sorted(parameters.keys()), parameters


    def status(self):
        dirs = csys.list_dir(self.path)
        if self.is_locked(): return "locked"
        running = False
        for run in dirs:
            if run.startswith("run.") or run.startswith("raw."):
                config_file = metadata.ConfigFile(os.path.join(self.path, run, "status.json"))
                status = config_file.read_variable("status")
                if status == "done":
                    return status
                if status == "failed":
                    return status
                if status == "running":
                    running = True
        if self.is_raw():
            return "raw"
        if running:
            return "running"
        return "submitted"

    def outputs(self):
        if self.machine_id is None:
            path = os.path.join(self.path, "rawdata")
            return csys.list_dir(path)
        path = os.path.join(self.path, self.machine_id, "outputs")
        if not os.path.exists(path):
            return []
        dirs = csys.list_dir(path)
        return dirs

    def collect(self, impression):
        workflow_id = self.workflow_id()
        workflow = VWorkflow(os.path.join(self.path, workflow_id))
        workflow.collect(impression)

    def get_file(self, filename):
        dirs = csys.list_dir(self.path)
        for run in dirs:
            if run.startswith("run.") or run.startswith("raw."):
                if filename == "stdout":
                    return os.path.join(self.path, run, filename)
                else:
                    return os.path.join(self.path, run, "output", filename)


    def kill(self):
        ps = subprocess.Popen("docker kill {0}".format(self.container_id()),
                              shell=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        ps.wait()

    def start(self):
        ps = subprocess.Popen("docker start -a {0}".format(self.container_id()),
                              shell=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)

        run_path = os.path.join(self.path, self.machine_storage())
        config_file = metadata.ConfigFile(os.path.join(run_path, "status.json"))
        config_file.write_variable("docker_run_pid", ps.pid)
        out = ""
        while ps.poll() is None:
            stdout = ps.stdout
            if stdout is None:
                continue
            line = stdout.readline().decode()
                # line = line.strip()
            if line:
                out += line

        run_path = os.path.join(self.path, self.machine_storage())
        stdout = os.path.join(run_path, "stdout")
        with open(stdout, "w") as f:
            f.write(out)
        return (ps.poll() == 0)

    def remove(self):
        ps = subprocess.Popen("docker rm -f {0}".format(self.container_id()),
                              shell=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        print(ps.stdout.read().decode())
        if ps.poll() == 0:
            print("Successful removed")
            shutil.rmtree(self.path)

    def check(self):
        run_path = os.path.join(self.path, self.machine_storage())
        status_file = metadata.ConfigFile(os.path.join(run_path, "status.json"))
        status_file.write_variable("status", "running")
        try:
            self.create_arguments_file()
            self.create_container()
            self.copy_arguments_file()
            status = self.start()
        except Exception as e:
            status_file.write_variable("status", "failed")
            self.append_error(str(e))
            raise e
        if status :
            status_file.write_variable("status", "done")
        else:
            status_file.write_variable("status", "failed")
            self.append_error("Run error")

    def execute(self):
        run_path = os.path.join(self.path, self.machine_storage())
        status_file = metadata.ConfigFile(os.path.join(run_path, "status.json"))
        status_file.write_variable("status", "running")
        try:
            self.create_arguments_file()
            self.create_container()
            self.copy_arguments_file()
            status = self.start()
        except Exception as e:
            status_file.write_variable("status", "failed")
            self.append_error(str(e))
            raise e
        if status :
            status_file.write_variable("status", "done")
        else:
            status_file.write_variable("status", "failed")
            self.append_error("Run error")
