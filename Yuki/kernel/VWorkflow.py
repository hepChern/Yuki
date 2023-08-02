"""
Construction of a workflow with the jobs, especially from the task
"""
import os
from Chern import utils
from Chern.utils import csys 
from Chern.utils import metadata
from Chern.kernel.ChernCache import ChernCache
from Yuki.kernel.VJob import VJob
import time
import json
# Comments:
# To use the reana api, we need the environment variable REANA_SERVER_URL
# However, setting the environment variable in the python script "might" not work
# Maybe we can try the execv function in the os module, let me see
cherncache = ChernCache.instance()

class VWorkflow(object):
    def __init__(self, job):
        """ Initialize it with a job
        """
        # Create a uuid for the workflow
        self.uuid = csys.generate_uuid()
        self.path = os.path.join(os.environ["HOME"], ".Yuki", "Workflows", self.uuid)
        self.start_job = job
        self.yaml_file = None # YamlFile()
        self.jobs = []
        self.machine_id = job.machine_id
        self.set_enviroment(self.machine_id)
        self.access_token = self.get_access_token(self.machine_id)

    def get_access_token(self, machine_id):
        path = os.path.join(os.environ["HOME"], ".Yuki", "runner_config.json")
        config_file = metadata.ConfigFile(path)
        tokens = config_file.read_variable("tokens", {})
        token = tokens.get(machine_id, "")
        return token

    def set_enviroment(self, machine_id):
        # Set the enviroment variable
        path = os.path.join(os.environ["HOME"], ".Yuki", "runner_config.json")
        config_file = metadata.ConfigFile(path)
        urls = config_file.read_variable("urls", {})
        url = urls.get(machine_id, "")
        os.environ["REANA_SERVER_URL"] = url


    def ping(self):
        # Ping the server
        # We must import the client here because we need to set the enviroment variable first
        from reana_client.api import client
        return client.ping(self.access_token)

    def create_workflow(self):
        # create a workflow
        from reana_client.api import client
        # name,
        # access_token,
        # workflow_json=None,
        # workflow_file=None,
        # parameters=None,
        # workflow_engine="yadage",
        # outputs=None,
        client.create_workflow_from_json(
            self.get_name(),
            self.get_access_token(self.machine_id),
            self.get_workflow_json(),
            None,
            None,
            "serial",
            None)

    def get_name(self):
        return "w-" + self.uuid[:8]

    def get_workflow_json(self):
        workflow_file = os.path.join(self.path, "workflow.json")
        csys.mkdir(self.path)
        d = {}
        d["version"] = "0.6.0"
        d["inputs"] = {}
        d["outputs"] = {}
        d["steps"] = []
        # json.dump(d, open(workflow_file, "w"))
        return d
        # return workflow_file

    def get_workflow(self, job):
        last_consult_time = cherncache.consult_table.get(job.path, -1)
        if time.time() - last_consult_time < 1: return
        cherncache.consult_table[job.path] = time.time()

        # Even if the job is finished, we still need to add it to the workflow, 
        # because we need to upload the files
        if job.status() == "finished":
            self.jobs.append(job)
            return

        for dependence in job.dependencies():
            print(dependence)
            path = os.path.join(os.environ["HOME"], ".Yuki", "Storage", dependence)
            print(path)
            self.get_workflow(VJob(path, self.machine_id))
        self.jobs.append(job)

    def construct(self):
        self.writeline("version 0.1.0")
        # Write the code name for jobs
        for job in self.jobs:
            if job.object_type == "algorithm":
                self.writeline(job.to_yaml())

        for job in self.jobs:
            if job.object_type == "task":
                self.writeline(job.to_yaml())

    def upload(self, jobs):
        for job in jobs:
            if job.require_upload:
                job.upload()
    
    def run_workflow(self):
        # use the reana api to run the workflow
        pass

    def check_status(self):
        # Check the status of the workflow
        # Check whether the workflow is finished, every 5 seconds
        while True:
            status = self.get_status()
            if status == "finished":
                break
            time.sleep(5)
        pass



    def writeline(self, line):
        self.yaml_file.writeline(line)

    def write_to(self):
        pass

    def run(self):
        self.get_workflow(self.start_job)
        path = [job.path for job in self.jobs]
        self.create_workflow()
        return ""
        self.construct()
        self.write_to()
        self.upload()
        self.run_workflow()
        self.check_status()
        self.download()

    def download(self):
        pass
