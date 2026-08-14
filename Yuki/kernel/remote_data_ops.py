"""Remote-side data operations for register-data.

Helpers that build shell commands executed ON an ssh runner, plus the
local Yuki-side storage paths for registration job state.
"""
import json
import os
import shlex

from CelebiChrono.utils.metadata import ConfigFile

REMOTE_MD5_SCRIPT = r'''
import hashlib, os, sys

def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def dir_md5(root):
    total = hashlib.md5()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        files = [f for f in files if not f.startswith(".")]
        dirs.sort()
        files.sort()
        for name in files:
            total.update(md5sum(os.path.join(cur, name)).encode("utf-8"))
    return total.hexdigest()

print(dir_md5(sys.argv[1]))
'''


def _yuki_dir():
    """Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def remote_md5_command(remote_path):
    """SSH command computing the dir md5 on the remote host."""
    return f"python3 -c {shlex.quote(REMOTE_MD5_SCRIPT)} {shlex.quote(remote_path)}"


def build_remote_fast_copy_command(src, dst):
    """Copy src into dst on the remote host, fastest mechanism first.

    Mirrors yuki_create_data.fast_copy_tree: reflink -> hardlink ->
    rsync -> plain copy.
    """
    return (
        f"mkdir -p {shlex.quote(dst)} && "
        f"(cp -a --reflink=auto {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"cp -al {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"rsync -a {shlex.quote(src)}/ {shlex.quote(dst)}/ || "
        f"cp -r {shlex.quote(src)}/. {shlex.quote(dst)}/)"
    )


JOBS_DIR_NAME = "register-jobs"


def _jobs_dir(yuki_dir):
    return os.path.join(yuki_dir, JOBS_DIR_NAME)


def write_job_state(yuki_dir, job_id, state):
    """Persist a registration job's state to $YUKIDIR/register-jobs/<id>.json."""
    os.makedirs(_jobs_dir(yuki_dir), exist_ok=True)
    with open(os.path.join(_jobs_dir(yuki_dir), f"{job_id}.json"),
              "w", encoding="utf-8") as f:
        json.dump(state, f)


def read_job_state(yuki_dir, job_id):
    """Read a registration job's state, or None."""
    path = os.path.join(_jobs_dir(yuki_dir), f"{job_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _impression_md5(imp_dir):
    from CelebiChrono.utils import metadata
    yaml_file = metadata.YamlFile(os.path.join(imp_dir, "contents", "celebi.yaml"))
    return yaml_file.read_variable("uuid", "")


def find_existing_registration(yuki_dir, runner_id, remote_path):
    """Return the completed registration for (runner, path), else None."""
    storage = os.path.join(yuki_dir, "Storage")
    if not os.path.isdir(storage):
        return None
    for proj in os.listdir(storage):
        proj_dir = os.path.join(storage, proj)
        if not os.path.isdir(proj_dir):
            continue
        for imp in os.listdir(proj_dir):
            marker_path = os.path.join(proj_dir, imp, "remote.json")
            if not os.path.exists(marker_path):
                continue
            marker = ConfigFile(marker_path)
            if marker.read_variable("host_runner_id", "") == runner_id and \
                    marker.read_variable("source_path", "") == remote_path:
                return {"result": {
                    "uuid": _impression_md5(os.path.join(proj_dir, imp)),
                    "impression_uuid": imp,
                }}
    return None


def find_inflight_job(yuki_dir, runner_id, remote_path):
    """Return the job id of an in-flight registration for (runner, path)."""
    jobs_dir = _jobs_dir(yuki_dir)
    if not os.path.isdir(jobs_dir):
        return None
    for name in os.listdir(jobs_dir):
        if not name.endswith(".json"):
            continue
        state = read_job_state(yuki_dir, name[:-5])
        if state is None:
            continue
        if state.get("runner_id") == runner_id and \
                state.get("remote_path") == remote_path and \
                state.get("status") not in ("done", "failed"):
            return name[:-5]
    return None
