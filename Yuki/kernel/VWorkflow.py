"""
Construction of a workflow with the jobs, especially from the task.

This module provides the VWorkflow class which manages workflow execution
and coordination between jobs using the REANA workflow management system.
"""
import os
import time

from Chern.utils import csys
from Chern.utils import metadata
from Chern.kernel.chern_cache import ChernCache
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VContainer import VContainer
from Yuki.kernel.VImage import VImage
from Yuki.utils import snakefile

# Comments:
# To use the reana api, we need the environment variable REANA_SERVER_URL
# However, setting the environment variable in the python script "might" not work
# Maybe we can try the execv function in the os module, let me see
# It seems to works at my MacOS, but I don't know whether it will still work at, for example, Ubuntu
CHERN_CACHE = ChernCache.instance()

class VWorkflow:
    """
    Virtual Workflow class for managing job execution workflows.

    This class handles the construction and execution of workflows
    containing multiple jobs, managing dependencies and orchestrating
    execution through the REANA workflow management system.
    """
    uuid = None

    def __init__(self, jobs, uuid=None):
        """Initialize workflow with jobs and optional UUID."""
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
            print("The start job(s) are", self.start_job)
            self.machine_id = self.start_job[0].machine_id
            self.config_file.write_variable("machine_id", self.machine_id)

        # FIXME: if it is not the starting of the workflow, one should read the
        # information from bookkeeping, except for the access_token
        self.yaml_file = None  # YamlFile()
        self.jobs = []

        # Initialize attributes that may be set later
        self.snakefile_path = None
        self.dependencies = None
        self.steps = None

        self.set_enviroment(self.machine_id)
        self.access_token = self.get_access_token(self.machine_id)

    def get_name(self):
        """Get the workflow name."""
        return "w-" + self.uuid[:8]

    def run(self):
        """
        Run the workflow:
        1. Construct the workflow from the start_job
        2. Set all the jobs to be the waiting status
        3. Check the dependencies
        4. Run
        """
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
            print(f"job machine: {job.machine_id}")

        for job in self.jobs:
            if job.is_input:
                continue
            job.set_status("waiting")

        # First, check whether the dependencies are satisfied
        while True:
            all_finished = True
            for job in self.jobs:
                print(f"Check the job {job}", job)
                if not job.is_input:
                    continue
                if job.status() == "archived":
                    continue
                print(f"Check the status of workflow {job.workflow_id()}")
                workflow = VWorkflow([], job.workflow_id())
                if workflow:
                    workflow.update_workflow_status()
                status = workflow.status()
                job.update_status_from_workflow(status)
                if job.status() != "finished":
                    all_finished = False
                    break
                # FIXME: may check if some of the dependence fail
            if all_finished:
                break
            time.sleep(10)

        for job in self.jobs:
            if job.is_input:
                continue
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
        """Kill the workflow execution."""
        from reana_client.api import client
        client.stop_workflow(
            self.get_name(),
            False,
            self.get_access_token(self.machine_id)
        )

    def construct_workflow_jobs(self, job):
        """Construct workflow jobs recursively including dependencies."""
        last_consult_time = CHERN_CACHE.consult_table.get(job.path, -1)
        if time.time() - last_consult_time < 1:
            return
        CHERN_CACHE.consult_table[job.path] = time.time()

        if job.machine_id is None:
            job = VJob(job.path, self.machine_id)

        if job.machine_id is None:
            return

        # Even if the job is finished, we still need to add it to the workflow,
        # because we need to upload the files
        if job.status() == "finished":
            if job.object_type() == "task":
                job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "failed":
            if job.object_type() == "task":
                job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "pending" or job.status() == "running":
            if job.object_type() == "task":
                job.is_input = True
            self.jobs.append(job)
            return

        if job.status() == "archived":
            if job.object_type() == "task":
                job.is_input = True
            self.jobs.append(job)
            return

        for dependence in job.dependencies():
            path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", dependence)
            self.construct_workflow_jobs(VJob(path, None))
        self.jobs.append(job)

    def create_workflow(self):
        """Create a workflow using REANA client."""
        from reana_client.api import client
        self.set_enviroment(self.machine_id)

        reana_json = {"workflow": {}}
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
        """Get list of all files from jobs."""
        files = []
        for job in self.jobs:
            files.extend(job.files())
        return files

    def parameters(self):
        """Get workflow parameters."""
        return []

    def get_file_list(self):
        """Get file list (broken method - needs fixing)."""
        # This method references undefined 'jobs' variable
        # Should use self.jobs instead
        for job in self.jobs:
            for filename in job.files():
                file = f"impression/{job.impression()}/{filename}"
                # self.files doesn't exist - this method needs to be fixed
                # self.files.append(file)

    def get_parameters(self):
        """Get parameters (broken method - needs fixing)."""
        # This method references undefined 'jobs' variable
        # Should use self.jobs instead
        pass
        # job.parameters doesn't exist in VJob
        # for parameter in job.parameters:
        #     parname = f"par_{job.impression()}_{parameter}"
        #     value = self.get_parameter(parameter)  # This method doesn't exist
        #     self.parameters[parname] = value  # self.parameters is not a dict

    def get_steps(self):
        """Get workflow steps (broken method - needs fixing)."""
        steps = []  # Initialize local variable
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


    def construct_snake_file(self):
        """Construct snakemake file for workflow execution."""
        self.snakefile_path = os.path.join(self.path, "Snakefile")
        snake_file = snakefile.SnakeFile(os.path.join(self.path, "Snakefile"))

        self.dependencies = {}
        self.steps = []

        snake_file.addline("rule all:", 0)
        snake_file.addline("input:", 1)
        self.dependencies["all"] = []
        for job in self.jobs:
            snake_file.addline(f'"{job.short_uuid()}.done",', 2)
            self.dependencies["all"].append(f"step{job.short_uuid()}")

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
            snake_file.addline(f"rule step{job.short_uuid()}:", 0)
            snake_file.addline("input:", 1)
            for input_file in snakemake_rule["inputs"]:
                snake_file.addline(f'"{input_file}",', 2)
            snake_file.addline("output:", 1)
            snake_file.addline(f'"{job.short_uuid()}.done"', 2)
            snake_file.addline("container:", 1)
            snake_file.addline(f'"docker://{snakemake_rule["environment"]}"', 2)
            snake_file.addline("resources:", 1)
            snake_file.addline(f'kubernetes_memory_limit="{snakemake_rule["memory"]}"', 2)
            snake_file.addline("shell:", 1)
            snake_file.addline(f'"{" && ".join(snakemake_rule["commands"])}"', 2)

            self.steps.append(step)

        snake_file.write()

    def get_access_token(self, machine_id):
        """Get access token for the specified machine."""
        path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
        config_file = metadata.ConfigFile(path)
        tokens = config_file.read_variable("tokens", {})
        token = tokens.get(machine_id, "")
        return token

    def set_enviroment(self, machine_id):
        """Set the environment variable for REANA server URL."""
        # Set the environment variable
        path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
        config_file = metadata.ConfigFile(path)
        urls = config_file.read_variable("urls", {})
        url = urls.get(machine_id, "")
        from reana_client.api import client
        from reana_commons.api_client import BaseAPIClient
        os.environ["REANA_SERVER_URL"] = url
        BaseAPIClient("reana-server")


    def upload_file(self):
        """Upload files to REANA workflow."""
        from reana_client.api import client
        self.set_enviroment(self.machine_id)
        for job in self.jobs:
            for name in job.files():
                print(f"upload file: {name}")
                with open(os.path.join(job.path, "contents", name[8:]), "rb") as f:
                    client.upload_file(
                        self.get_name(),
                        f,
                        "imp" + name,
                        self.get_access_token(self.machine_id)
                    )
            if job.environment() == "rawdata":
                filelist = os.listdir(os.path.join(job.path, "rawdata"))
                for filename in filelist:
                    with open(os.path.join(job.path, "rawdata", filename), "rb") as f:
                        client.upload_file(
                            self.get_name(),
                            f,
                            "imp" + job.short_uuid() + "/" + filename,
                            self.get_access_token(self.machine_id)
                        )
            elif job.is_input:
                impression = job.path.split("/")[-1]
                print(f"Downloading the files from impression {impression}")
                path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", impression, job.machine_id)
                if not os.path.exists(os.path.join(path, "outputs")):
                    workflow = VWorkflow([], job.workflow_id())
                    workflow.download(impression)

                filelist = os.listdir(os.path.join(path, "outputs"))
                for filename in filelist:
                    with open(os.path.join(path, "outputs", filename), "rb") as f:
                        client.upload_file(
                            self.get_name(),
                            f,
                            "imp"+job.short_uuid() + "/outputs/" + filename,
                            self.get_access_token(self.machine_id)
                        )

        with open(self.snakefile_path, "rb") as f:
            client.upload_file(
                self.get_name(),
                f,
                "Snakefile",
                self.get_access_token(self.machine_id)
            )
        yaml_file = metadata.YamlFile(os.path.join(self.path, "reana.yaml"))
        yaml_file.write_variable("workflow", {
            "type": "snakemake",
            "file": "Snakefile",
            })
        with open(os.path.join(self.path, "reana.yaml"), "rb") as f:
            client.upload_file(
                self.get_name(),
                f,
                "reana.yaml",
                self.get_access_token(self.machine_id)
            )

    def check_status(self):
        """Check the status of the workflow periodically."""
        # Check the status of the workflow
        # Check whether the workflow is finished, every 5 seconds
        counter = 0
        while True:
            # Check the status every minute
            if counter % 60 == 0:
                self.update_workflow_status()

            status = self.status()
            if status in ('finished', 'failed'):
                return status
            time.sleep(1)
            counter += 1

    def set_workflow_status(self, status):
        """Set the workflow status."""
        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results = results_file.read_variable("results", {})
        results["status"] = status
        results_file.write_variable("results", results)

    def update_workflow_status(self):
        """Update workflow status from REANA."""
        from reana_client.api import client
        self.set_enviroment(self.machine_id)
        results = client.get_workflow_status(
            self.get_name(),
            self.get_access_token(self.machine_id))
        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results_file.write_variable("results", results)

    def status(self):
        """Get the current workflow status."""
        status, last_consult_time = CHERN_CACHE.consult_table.get(self.uuid, ("unknown", -1))
        if time.time() - last_consult_time < 1:
            return status

        path = os.path.join(self.path, "results.json")
        results_file = metadata.ConfigFile(path)
        results = results_file.read_variable("results", {})
        status = results.get("status", "unknown")
        CHERN_CACHE.consult_table[self.uuid] = (status, time.time())
        return status

    def writeline(self, line):
        """Write a line to the YAML file."""
        self.yaml_file.writeline(line)

    def start_workflow(self):
        """Start the workflow execution."""
        from reana_client.api import client
        self.set_enviroment(self.machine_id)
        client.start_workflow(
            self.get_name(),
            self.get_access_token(self.machine_id),
            {}
        )

    def download(self, impression=None):
        """Download workflow results."""
        print("Downloading the files")
        from reana_client.api import client
        self.set_enviroment(self.machine_id)
        if impression:
            files = client.list_files(
                self.get_name(),
                self.get_access_token(self.machine_id),
                "imp"+impression[0:7]+"/outputs"
            )
            path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", impression, self.machine_id)
            print(f"Files: {files}")
            for file in files:
                print(f'Downloading {file["name"]}')
                output = client.download_file(
                    self.get_name(),
                    file["name"],
                    self.get_access_token(self.machine_id),
                )
                print(f'Downloading {file["name"]}')
                os.makedirs(os.path.join(path, "outputs"), exist_ok=True)
                filename = os.path.join(path, file["name"][11:])
                with open(filename, "wb") as f:
                    f.write(output[0])

    def ping(self):
        """Ping the REANA server (FIXME: This function is not used)."""
        # Ping the server
        # We must import the client here because we need to set the environment variable first
        from reana_client.api import client
        self.set_enviroment(self.machine_id)
        return client.ping(self.access_token)
