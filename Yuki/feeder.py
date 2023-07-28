import uuid
import os
import tarfile
from Chern.utils import csys
from Chern.utils import metadata
def feed(impression, path):
    dst = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    print(dst)
    if not csys.exists(dst):
        print("Impression {} does not exists.".format(impression))
        return
    uid = "raw." + uuid.uuid4().hex
    print(path, os.path.join(dst, uid, "output"))
    csys.copy_tree(path, os.path.join(dst, uid, "output"))
    config_file = metadata.ConfigFile(os.path.join(dst, uid, "status.json"))
    config_file.write_variable("status", "done")
