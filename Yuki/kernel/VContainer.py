"""

"""
import os
from Chern.utils import csys
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VImage import VImage

class VContainer(VJob):
    """
    """
    def __init__(self, path, machine_id):
        super(VContainer, self).__init__(path, machine_id)

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
        commands = ["mkdir -p imp{}".format(self.short_uuid())]
        commands.append("cd imp{}".format(self.short_uuid()))
        if self.is_input:
            raw_commands = []
        else:
            raw_commands = self.image().yaml_file.read_variable("commands", [])
        for command in raw_commands:
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
                command = command.replace(name, f"$REANA_WORKSPACE/imp{impression[:7]}")
            command = command.replace("${workspace}", "$REANA_WORKSPACE")
            command = command.replace("${output}", f"imp{self.short_uuid()}")
            image = self.image()
            if image:
                command = command.replace("${code}", f"$REANA_WORKSPACE/imp{image.short_uuid()}")
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
        commands = ["mkdir -p imp{}".format(self.short_uuid())]
        commands.append("cd imp{}".format(self.short_uuid()))
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
                command = command.replace(name, f"$REANA_WORKSPACE/imp{impression[:7]}")
            command = command.replace("${workspace}", "$REANA_WORKSPACE")
            command = command.replace("${output}", f"imp{self.short_uuid()}")
            image = self.image()
            if image:
                command = command.replace("${code}", f"$REANA_WORKSPACE/imp{image.short_uuid()}")
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

    def parameters(self):
        """
        Read the parameters file
        """
        parameters = self.yaml_file.read_variable("parameters", {})
        return sorted(parameters.keys()), parameters

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

