import os, sys
import json
import subprocess
import time
from Chern.utils import csys
from Chern.utils import metadata
from Yuki.kernel.VJob import VJob
# from Yuki.kernel.VWorkflow import VWorkflow
"""
This should have someting
A image can be determined uniquely by the ?
"""
import logging
class VImage(VJob):
    def __init__(self, path, machine_id):
        super(VImage, self).__init__(path, machine_id)

        def image_id(self):
        dirs = csys.list_dir(self.path)
        for run in dirs:
            if run.startswith("run."):
                config_file = metadata.ConfigFile(os.path.join(self.path, run, "status.json"))
                status = config_file.read_variable("status", "submitted")
                if status == "built":
                    return config_file.read_variable("image_id")
        return ""

    def step(self):
        commands = ["mkdir -p {}".format(self.short_uuid())]
        commands.append("cd {}".format(self.short_uuid()))

        compile_rules = self.yaml_file.read_variable("compile", [])
        for rule in compile_rules:
            # Replace the ${code} with the code path
            rule = rule.replace("${code}", self.short_uuid())
            rule = rule.replace("${workspace}", "$REANA_WORKSPACE")
            commands.append(rule)

        commands.append("cd $REANA_WORKSPACE")
        commands.append("touch {}.done".format(self.short_uuid()))
        step = {}
        step["inputs"] = []
        step["commands"] = commands
        step["environment"] = self.environment()
        step["memory"] = self.memory()
        step["name"] = "step{}".format(self.short_uuid())
        return step

    def snakemake_rule(self):
        commands = ["mkdir -p {}".format(self.short_uuid())]
        commands.append("cd {}".format(self.short_uuid()))

        compile_rules = self.yaml_file.read_variable("compile", [])
        for rule in compile_rules:
            # Replace the ${code} with the code path
            rule = rule.replace("${code}", self.short_uuid())
            rule = rule.replace("${workspace}", "$REANA_WORKSPACE")
            commands.append(rule)

        commands.append("cd $REANA_WORKSPACE")
        commands.append("touch {}.done".format(self.short_uuid()))
        step = {}
        step["inputs"] = []
        step["commands"] = commands
        step["environment"] = self.environment()
        step["memory"] = self.memory()
        step["name"] = "step{}".format(self.short_uuid())

        return step

    def environment(self):
        return self.yaml_file.read_variable("environment", "reanahub/reana-env-root6:6.18.04")

    def memory(self):
        return self.yaml_file.read_variable("kubernetes_memory_limit", "256Mi")
