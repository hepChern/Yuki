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

    def inputs(self):
        """
        Input data.
        """
        print("Check the inputs of the image")
        alias_to_imp = self.config_file.read_variable("alias_to_impression", {})
        print(alias_to_imp)
        return (alias_to_imp.keys(), alias_to_imp)

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
        commands = ["mkdir -p imp{}".format(self.short_uuid())]
        commands.append("cd imp{}".format(self.short_uuid()))

        # Add ln -s $REANA_WORKSPACE/{alias} {alias} to the commands
        alias_list, alias_map = self.inputs()
        for alias in alias_list:
            impression = alias_map[alias]
            command = f"ln -s $REANA_WORKSPACE/imp{impression[:7]} {alias}"
            commands.append(command)

        compile_rules = self.yaml_file.read_variable("build", [])
        for rule in compile_rules:
            # Replace the ${code} with the code path
            rule = rule.replace("${workspace}", "$REANA_WORKSPACE")
            rule = rule.replace("${code}", f"$REANA_WORKSPACE/imp{self.short_uuid()}")

            alias_list, alias_map = self.inputs()
            for alias in alias_list:
                impression = alias_map[alias]
                rule = rule.replace("${"+ alias +"}", f"$REANA_WORKSPACE/imp{impression[:7]}")
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
        commands = ["mkdir -p imp{}".format(self.short_uuid())]
        commands.append("cd imp{}".format(self.short_uuid()))

        # Add ln -s $REANA_WORKSPACE/{alias} {alias} to the commands
        alias_list, alias_map = self.inputs()
        for alias in alias_list:
            impression = alias_map[alias]
            command = f"ln -s $REANA_WORKSPACE/imp{impression[:7]} {alias}"
            commands.append(command)

        compile_rules = self.yaml_file.read_variable("build", [])
        for rule in compile_rules:
            # Replace the ${code} with the code path
            rule = rule.replace("${workspace}", "$REANA_WORKSPACE")
            rule = rule.replace("${code}", f"$REANA_WORKSPACE/imp{self.short_uuid()}")

            alias_list, alias_map = self.inputs()
            for alias in alias_list:
                impression = alias_map[alias]
                rule = rule.replace("${"+ alias +"}", f"$REANA_WORKSPACE/imp{impression[:7]}")
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

    def default_environment(self):
        return "docker.io/reanahub/reana-env-root6:6.18.04"

    def environment(self):
        environment = self.yaml_file.read_variable("environment", self.default_environment())
        if environment == "script":
            return self.default_environment()
        return environment

    def memory(self):
        return self.yaml_file.read_variable("kubernetes_memory_limit", "256Mi")
