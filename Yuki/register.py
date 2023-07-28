"""
Register a Chern machine
"""
import os
import uuid
from Chern.utils import metadata
def register():
    config_file = metadata.ConfigFile(os.path.join(os.environ["HOME"], ".Yuki/config.json"))
    config_file.write_variable("machine_id", uuid.uuid4().hex)
    config_file.write_variable("runner_type", "docker")



