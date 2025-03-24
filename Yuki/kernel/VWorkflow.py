"""
Construction of a workflow with the jobs, especially from the task
"""
import os
from Chern.utils import csys
from Chern.utils import metadata
from Chern.kernel.chern_cache import ChernCache
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VContainer import VContainer
from Yuki.kernel.VImage import VImage
import time
from Yuki.utils import snakefile

# Comments:
# To use the reana api, we need the environment variable REANA_SERVER_URL
# However, setting the environment variable in the python script "might" not work
# Maybe we can try the execv function in the os module, let me see
# It seems to works at my MacOS, but I don't know whether it will still work at, for example, Ubuntu
cherncache = ChernCache.instance()

class VWorkflow(object):
    uuid = None
    def __init__(self, jobs, uuid = None):
        """ Initialize it with a job
        """
        # Create a uuid for the workflow
        if uuid:
            self.uuid = uuid
            self.start_job = None
            self.path = os.path.join(os.environ["HOME"], ".Yuki", "Workflows", self.uuid)
            self.config_file = metadata.ConfigFile(os.path.join(self.path, "config.json"))
            self.machine_id = self.config_file.read_variable("machine_id", "")
        else:
            self.uuid = csys.generate_uuid()
            self.start_job = jobs.copy()
            self.path = os.path.join(os.environ["HOME"], ".Yuki", "Workflows", self.uuid)
            self.config_file = metadata.ConfigFile(os.path.join(self.path, "config.json"))
            self.machine_id = self.start_job[0].machine_id
            self.config_file.write_variable("machine_id", self.machine_id)

        # FIXME: if it is not the starting of the workflow, one should read the information from bookkeeping, except for the access_token
        self.yaml_file = None # YamlFile()
        self.jobs = []
        self.set_enviroment(self.machine_id)
        self.access_token = self.get_access_token(self.machine_id)

    def get_name(self):
        return "w-" + self.uuid[:8]

    """ Run the workflow:
    1. Construct the workflow from the start_job
    2. Set all the jobs to be the waiting status
    3. Check the dependencies
    4. Run
    """
    def run(self):
        # Construct the workflow
        print("Constructing the workflow")
        print(f"Start job: {self.start_job}")
        for job in self.start_job:
            self.construct_workflow_jobs(job)

        print(f"Jobs after the construction: {self.jobs}")
        # Set all the jobs to be the waiting status
        for job in self.jobs:
            print(f"job: {job}, is input: {job.is_input}")
            print(f"job status: {job.status()}")

        for job in self.jobs:
            if job.is_input: continue
            job.set_status("waiting")

        # First, check whether the dependencies are satisfied
        while True:
            all_finished = True
            for job in self.jobs:
                if not job.is_input: continue
                if job.status() == "archived": continue
                workflow = VWorkflow([], job.workflow_id())
                if workflow:
                    workflow.update_workflow_status()
                status = workflow.status()
                job.update_status_from_workflow(status)
                if job.status() != "finished":
                    all_finished = False
                    break
            if all_finished: break
            time.sleep(10)

        for job in self.jobs:
            if job.is_input: continue
            job.set_workflow_id(self.uuid)
            job.set_status("running")

        try:
            print("Constructing the snakefile")
            self.construct_snake_file()
        except:
            print("Failed to construct the snakefile")
            self.set_workflow_status("failed")
            for job in self.jobs:
                job.set_status("failed")
            raise

        try:
            print("Creating the workflow")
            self.create_workflow()
        except:
            print("Failed to create the workflow")
            self.set_workflow_status("failed")
            for job in self.jobs:
                job.set_status("failed")
            raise

        try:
            self.upload_file()
        except:
            self.set_workflow_status("failed")
            for job in self.jobs:
                job.set_status("failed")
            raise

        try:
            self.start_workflow()
        except:
            self.set_workflow_status("failed")
            for job in self.jobs:
                job.set_status("failed")
            raise

        self.check_status()
        self.download()



    def kill(self):
        from reana_client.api import client
        client.stop_workflow(
            self.get_name(),
            False,
            self.get_access_token(self.machine_id)
        )


    def construct_workflow_jobs(self, job):
        last_consult_time = cherncache.consult_table.get(job.path, -1)
        if time.time() - last_consult_time < 1: return
        cherncache.consult_table[job.path] = time.time()

        # Even if the job is finished, we still need to add it to the workflow,
        # because we need to upload the files
        if job.status() == "finished":
            if job.object_type() == "task": job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "failed":
            if job.object_type() == "task": job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "pending" or job.status() == "running":
            if job.object_type() == "task": job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "archived":
            if job.object_type() == "task": job.is_input = True
            self.jobs.append(job)
            return

        for dependence in job.dependencies():
            path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", dependence)
            self.construct_workflow_jobs(VJob(path, self.machine_id))
        self.jobs.append(job)


    def create_workflow(self):
        # create a workflow
        from reana_client.api import client
        reana_json = dict(workflow={})
        reana_json["workflow"]["specification"] = {
                "job_dependencies": self.dependencies,
                "steps": self.steps,
                }
        reana_json["workflow"]["type"] = "snakemake"
        reana_json["workflow"]["file"] = "Snakefile"
        reana_json["inputs"] = {"files": self.get_files()}
        client.create_workflow(
                reana_json,
                self.get_name(),
                self.get_access_token(self.machine_id)
                )

    def get_files(self):
        files = []
        for job in self.jobs:
            files.extend(job.files())
        return files

    def parameters(self):
        return []

    def get_file_list(self):
        for job in jobs:
            for filename in job.files:
                file = "impression/{}/{}".format(job.impression(), filename)
                self.files.append(file)

    def get_parameters(self):
        for job in jobs:
            for parameter in job.parameters:
                parname = "par_{}_{}".format(job.impression(), parameter)
                value = self.get_parameter(parameter)
                self.parameters[parname] = value

    def get_steps(self):
        for job in self.jobs:
            if job.object_type() == "algorithm":
                # In this case, if the command is compile, we need to compile it
                pass
            if job.object_type() == "task":
                steps.append(VContainer(job.path, job.machine_id).step())
                # Replace the ${alg} -> algorithm folder
                # Replace the ${parameters} -> actual parameters
                # Replace the ${}
        return steps


    """  related to the snakemake
    """
    def construct_snake_file(self):
        self.snakefile_path = os.path.join(self.path, "Snakefile")
        snake_file = snakefile.SnakeFile(os.path.join(self.path, "Snakefile"))


        self.dependencies = {}
        self.steps = []

        snake_file.addline("rule all:", 0)
        snake_file.addline("input:", 1)
        self.dependencies["all"] = []
        for job in self.jobs:
            snake_file.addline("\"{}.done\",".format(job.short_uuid()), 2)
            self.dependencies["all"].append("step{}".format(job.short_uuid()))

        for job in self.jobs:
            if job.object_type() == "algorithm":
                # In this case, if the command is compile, we need to compile it
                image = VImage(job.path, job.machine_id)
                image.is_input = job.is_input
                snakemake_rule = image.snakemake_rule()
                step = image.step()

                # In this case, we also need to run the "touch"
            if job.object_type() == "task":
                container = VContainer(job.path, job.machine_id)
                container.is_input = job.is_input
                snakemake_rule = container.snakemake_rule()
                step = container.step()

            snake_file.addline("\n", 0)
            snake_file.addline("rule step{}:".format(job.short_uuid()), 0)
            snake_file.addline("input:", 1)
            for input_file in snakemake_rule["inputs"]:
                snake_file.addline("\""+input_file+"\"" + ",", 2)
            snake_file.addline("output:", 1)
            snake_file.addline("\"{}.done\"".format(job.short_uuid()), 2)
            snake_file.addline("container:", 1)
            snake_file.addline("\"docker://{}\"".format(snakemake_rule["environment"]), 2)
            snake_file.addline("resources:", 1)
            snake_file.addline("kubernetes_memory_limit=\"{}\"".format(snakemake_rule["memory"]), 2)
            snake_file.addline("shell:", 1)
            snake_file.addline("\""+" && ".join(snakemake_rule["commands"])+"\"", 2)

            self.steps.append(step)

        snake_file.write()

    """ Related to reana
    """
    def get_access_token(self, machine_id):
        path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
        config_file = metadata.ConfigFile(path)
        tokens = config_file.read_variable("tokens", {})
        token = tokens.get(machine_id, "")
        return token

    def set_enviroment(self, machine_id):
        # Set the enviroment variable
        path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
        config_file = metadata.ConfigFile(path)
        urls = config_file.read_variable("urls", {})
        url = urls.get(machine_id, "")
        os.environ["REANA_SERVER_URL"] = url

    def upload_file(self):
        from reana_client.api import client
        for job in self.jobs:
            for name in job.files():
                print("upload file: {}".format(name))
                client.upload_file(
                    self.get_name(),
                    open(os.path.join(job.path, "contents", name[8:]), "rb"),
                    "imp" + name,
                    self.get_access_token(self.machine_id)
                )
            if job.environment() == "rawdata":
                filelist = os.listdir(os.path.join(job.path, "rawdata"))
                for filename in filelist:
                    client.upload_file(
                        self.get_name(),
                        open(os.path.join(job.path, "rawdata", filename), "rb"),
                        "imp" + job.short_uuid() + "/" + filename,
                        self.get_access_token(self.machine_id)
                    )
            elif job.is_input:
                impression = job.path.split("/")[-1]
                print("Downloading the files from impression {}".format(impression))
                path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", impression, self.machine_id)
                if not os.path.exists(os.path.join(path, "outputs")):
                    workflow = VWorkflow([], job.workflow_id())
                    workflow.download(impression)

                filelist = os.listdir(os.path.join(path, "outputs"))
                for filename in filelist:
                    client.upload_file(
                        self.get_name(),
                        open(os.path.join(path, "outputs", filename), "rb"),
                        "imp"+job.short_uuid() + "/outputs/" + filename,
                        self.get_access_token(self.machine_id)
                    )

        client.upload_file(
            self.get_name(),
            open(self.snakefile_path, "rb"),
            "Snakefile",
            self.get_access_token(self.machine_id)
        )
        yaml_file = metadata.YamlFile(os.path.join(self.path, "reana.yaml"))
        yaml_file.write_variable("workflow", {
            "type": "snakemake",
            "file": "Snakefile",
            })
        client.upload_file(
            self.get_name(),
            open(os.path.join(self.path, "reana.yaml"), "rb"),
            "reana.yaml",
            self.get_access_token(self.machine_id)
        )

    def check_status(self):
        # Check the status of the workflow
        # Check whether the workflow is finished, every 5 seconds
        counter = 0
        while True:
            # Check the status every minute
            if counter % 60 == 0:
                self.update_workflow_status()

            status = self.status()
            if status == "finished" or status == "failed":
                return status
            time.sleep(1)
            counter += 1


    def set_workflow_status(self, status):
        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results = results_file.read_variable("results", {})
        results["status"] = status
        results_file.write_variable("results", results)

    def update_workflow_status(self):
        from reana_client.api import client
        results = client.get_workflow_status(
            self.get_name(),
            self.get_access_token(self.machine_id))
        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results_file.write_variable("results", results)

    def status(self):
        status, last_consult_time = cherncache.consult_table.get(self.uuid, ("unknown", -1))
        if time.time() - last_consult_time < 1: return status

        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results = results_file.read_variable("results", {})
        status = results.get("status", "unknown")
        cherncache.consult_table[self.uuid] = (status, time.time())
        return status

    def writeline(self, line):
        self.yaml_file.writeline(line)



    def start_workflow(self):
        from reana_client.api import client
        client.start_workflow(
            self.get_name(),
            self.get_access_token(self.machine_id),
            {}
        )

    def download(self, impression = None):
        print("Downloading the files")
        from reana_client.api import client
        if impression:
            files = client.list_files(
                self.get_name(),
                self.get_access_token(self.machine_id),
                "imp"+impression[0:7]+"/outputs"
            )
            path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", impression, self.machine_id)
            print("Files: {}".format(files))
            for file in files:
                print("Downloading {}".format(file["name"]))
                output = client.download_file(
                    self.get_name(),
                    file["name"],
                    self.get_access_token(self.machine_id),
                )
                print("Downloading {}".format(file["name"]))
                os.makedirs(os.path.join(path, "outputs"), exist_ok = True)
                filename = os.path.join(path, file["name"][11:])
                with open(filename, "wb") as f:
                    f.write(output[0])


    # FIXME: This function is not used
    def ping(self):
        # Ping the server
        # We must import the client here because we need to set the enviroment variable first
        from reana_client.api import client
        return client.ping(self.access_token)

