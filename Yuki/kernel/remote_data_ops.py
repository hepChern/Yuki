"""Remote-side data operations for register-data.

Helpers that build shell commands executed ON an ssh runner, plus the
local Yuki-side storage paths for registration job state.
"""
import json
import os
import shlex
import shutil
import tempfile

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
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        return None


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


def _ssh_settings(runner_id):
    """Merged ssh settings for a runner."""
    from Yuki.kernel import runner_config
    return runner_config.get_ssh_settings(runner_config.open_config(), runner_id)


def _ssh_connection(runner_id):
    """An _SshConnection for the runner (paramiko import stays lazy)."""
    from Yuki.kernel.ssh_workflow import _SshConnection
    settings = _ssh_settings(runner_id)
    return _SshConnection(host=settings.get("host", ""),
                          user=settings.get("user", ""),
                          key_path=settings.get("key_path"),
                          port=settings.get("port", 22))


def synthesize_impression(project_uuid, impression_uuid, data_md5, descriptor,
                          runner_id, source_path, managed_dir):
    """Create the impression record in Yuki Storage (data stays remote).

    Replicates yuki_create_data's layout (contents/ + config.json +
    status.json) and adds remote.json marking the hosting runner.
    """
    from CelebiChrono.utils import metadata
    from Yuki.cli.yuki_create_data import (
        create_canonical_rawdata_task, build_impression_config,
    )

    impression_dir = os.path.join(_yuki_dir(), "Storage",
                                  project_uuid, impression_uuid)
    os.makedirs(impression_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="yuki_register_") as tmp:
        create_canonical_rawdata_task(tmp, descriptor, data_md5)
        contents_dir = os.path.join(impression_dir, "contents")
        if os.path.exists(contents_dir):
            shutil.rmtree(contents_dir)
        shutil.copytree(tmp, contents_dir)
        impression_config = build_impression_config(
            project_uuid, impression_uuid, tmp)

    config_file = metadata.ConfigFile(os.path.join(impression_dir, "config.json"))
    for key, value in impression_config.items():
        config_file.write_variable(key, value)

    status_file = metadata.ConfigFile(os.path.join(impression_dir, "status.json"))
    status_file.write_variable("status", "pending")

    remote_file = metadata.ConfigFile(os.path.join(impression_dir, "remote.json"))
    remote_file.write_variable("host_runner_id", runner_id)
    remote_file.write_variable("source_path", source_path)
    remote_file.write_variable("remote_path", managed_dir)


def register_remote_data_job(runner_id, remote_path, project_uuid, descriptor,
                             update):
    """Run the registration pipeline: hash -> copy -> register.

    ``update(state: dict)`` is called with each progress state.
    """
    from CelebiChrono.kernel.vimpression import VImpression
    from Yuki.cli.yuki_create_data import create_canonical_rawdata_task

    update({"status": "hashing", "result": None, "error": None})
    with _ssh_connection(runner_id) as ssh:
        out, err, code = ssh.exec(remote_md5_command(remote_path), timeout=3600)
        if code != 0:
            raise RuntimeError(f"remote md5 failed: {err or out}")
        data_md5 = out.strip()
        if not data_md5:
            raise RuntimeError("remote md5 returned empty result")

        with tempfile.TemporaryDirectory(prefix="yuki_register_") as tmp:
            create_canonical_rawdata_task(tmp, descriptor, data_md5)
            impression_uuid = VImpression().generate_imp_uuid(
                project_uuid, tmp, [])

        settings = _ssh_settings(runner_id)
        managed_dir = (f"{settings.get('remote_workdir', '/tmp/yuki-workflows')}"
                       f"/impressions/{project_uuid}/{impression_uuid}")
        update({"status": "copying", "result": None, "error": None})
        out, err, code = ssh.exec(
            build_remote_fast_copy_command(remote_path, managed_dir),
            timeout=10800)
        if code != 0:
            raise RuntimeError(f"remote copy failed: {err or out}")

        update({"status": "registering", "result": None, "error": None})
        synthesize_impression(project_uuid, impression_uuid, data_md5,
                              descriptor, runner_id, remote_path, managed_dir)
        return {"uuid": data_md5, "impression_uuid": impression_uuid,
                "descriptor": descriptor}
