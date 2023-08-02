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
from Yuki.kernel.VWorkflow import VWorkflow
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
        workflow = VWorkflow(self)
        response = workflow.run()
        return response

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

    def add_input(self, path, alias):
        self.add_arc_from(path)
        self.set_alias(alias, path)

    def inputs(self):
        """
        Input data.
        """
        inputs = filter(lambda x: x.job_type() == "container",
                        self.predecessors())
        return list(map(lambda x: VContainer(x.path), inputs))


    def add_algorithm(self, path):
        """
        Add a algorithm
        """
        algorithm = self.algorithm()
        if algorithm is not None:
            print("Already have algorithm, will replace it")
            self.remove_algorithm()
        self.add_arc_from(path)

    def add_parameter(self, parameter, value):
        """
        Add a parameter to the parameters file
        """
        if parameter == "parameters":
            print("A parameter is not allowed to be called parameters")
            return
        parameters_file = utils.ConfigFile(self.path+"/.chern/parameters.py")
        parameters_file.write_variable(parameter, value)
        parameters = parameters_file.read_variable("parameters")
        if parameters is None:
            parameters = []
        parameters.append(parameter)
        parameters_file.write_variable("parameters", parameters)
        self.set_update_time()

    def storage(self):
        dirs = csys.list_dir(self.path)
        for run in dirs:
            if run.startswith("run.") or run.startswith("raw."):
                config_file = metadata.ConfigFile(os.path.join(self.path, run, "status.json"))
                status = config_file.read_variable("status", "submitted")
                if status == "done":
                    return run
        return ""

    def image(self):
        predecessors = self.predecessors()
        for pred_job in predecessors:
            if pred_job.job_type() == "image":
                return VImage(pred_job.path)
        return None

    def container_id(self):
        run_path = os.path.join(self.path, self.machine_storage())
        config_file = metadata.ConfigFile(os.path.join(run_path, "status.json"))
        container_id = config_file.read_variable("container_id")
        return container_id

    def impression(self):
        impression = self.config_file.read_variable("impressions")[-1]
        return impression

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
        parameters_file = metadata.ConfigFile(self.path+"/contents/parameters.json")
        parameters = parameters_file.read_variable("parameters", {})
        return sorted(parameters.keys()), parameters

    def create_arguments_file(self):
        try:
            parameters = self.parameters()
            parameters, values = self.parameters()
            parameter_str = ""
            for parameter in parameters:
                value = values[parameter]
                parameter_str += "        storage[\"{0}\"] = \"{1}\";\n".format(parameter, value)
            folder_str = ""
            for folder in self.inputs():
                alias = self.impression_to_alias(folder.impression())
                location = "/data/" + folder.impression()
                folder_str += "        storage[\"{0}\"] = \"{1}\";\n".format(alias, location)
            folder_str += "        storage[\"output\"] = \"/data/{0}\";\n".format(self.impression())
            argument_txt = """#ifndef CHERN_ARGUMENTS
#define CHERN_ARGUMENTS
#include <map>
#include <string>
namespace chern{{
class Parameters{{
  public:
    std::map<std::string, std::string> storage;

    Parameters() {{
{0}
    }}

    std::string operator [](std::string name) const {{
      return std::string(storage.at(name));
    }}
}};

class Folders{{
  public:
    std::map<std::string, std::string> storage;

    Folders() {{
{1}
    }}

    std::string operator [](std::string name) const {{
      return std::string(storage.at(name));
    }}
}};
}};
const chern::Parameters parameters;
const chern::Folders folders;
#endif
""".format(parameter_str, folder_str)
            with open(os.path.join(self.path, self.machine_storage(), "arguments"), "w") as f:
                f.write(argument_txt)
        except Exception as e:
            raise e

    def inspect(self):
        ps = subprocess.Popen("docker inspect {0}".format(self.container_id) )
        ps.wait()
        output = ps.communicate()[0]
        json_result = json.loads(output)
        return json_result[0]

    def is_raw(self):
        return csys.exists(os.path.join(self.path, "contents/data.json"))

    def is_locked(self):
        status_file = metadata.ConfigFile(os.path.join(self.path, "status.json"))
        status = status_file.read_variable("status")
        return status == "locked"

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
        dirs = csys.list_dir(self.path)
        for run in dirs:
            if run.startswith("run.") or run.startswith("raw."):
                return csys.list_dir(os.path.join(self.path, run, "output"))
        return []

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
