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
            data = json.load(f)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


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
                imp_dir = os.path.join(proj_dir, imp)
                # A failed registration must not count as existing; it can
                # be re-registered (synthesis overwrites the record).
                status = ConfigFile(
                    os.path.join(imp_dir, "status.json")
                ).read_variable("status", "")
                if status == "failed":
                    continue
                from CelebiChrono.utils import metadata
                yaml_file = metadata.YamlFile(
                    os.path.join(imp_dir, "contents", "celebi.yaml"))
                return {"result": {
                    "uuid": _impression_md5(imp_dir),
                    "impression_uuid": imp,
                    "descriptor": yaml_file.read_variable("descriptor", ""),
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


def list_managed_files(runner_id, managed_path):
    """List files in a runner's managed impressions dir via SSH.

    Returns [{"name": rel_path, "size": bytes}, ...] (flat, sorted by
    traversal order).
    """
    with _ssh_connection(runner_id) as ssh:
        return [{"name": rel, "size": size}
                for rel, _rpath, size in ssh.walk_files(managed_path)]


def _runner_name(runner_id):
    """Runner name for a runner id (fallback: the id itself)."""
    from Yuki.kernel import runner_config
    cfg = runner_config.open_config()
    runners_id = cfg.read_variable("runners_id", {})
    for name, rid in runners_id.items():
        if rid == runner_id:
            return name
    return runner_id


def verify_registered_data(project_uuid, impression_uuid):  # pylint: disable=too-many-locals
    """Recompute the data md5 and compare it with the registered uuid.

    Remote-hosted data is hashed on the host runner's managed dir; local
    data is hashed from Storage/<project>/<impression>/rawdata/.
    Returns {"match", "expected", "actual", "location", "error"}.
    """
    yuki_dir = _yuki_dir()
    imp_dir = os.path.join(yuki_dir, "Storage", project_uuid, impression_uuid)
    expected = _impression_md5(imp_dir)
    if not expected:
        return {"match": False, "expected": "", "actual": "",
                "location": "", "error": "no uuid registered for impression"}

    marker_path = os.path.join(imp_dir, "remote.json")
    if os.path.exists(marker_path):
        marker = ConfigFile(marker_path)
        host_runner = marker.read_variable("host_runner_id", "")
        managed_path = marker.read_variable("remote_path", "")
        # SSH connections to the runner flake occasionally (banner timeouts);
        # retry once, then report a proper error instead of a 500.
        last_exc = None
        for _attempt in range(2):
            try:
                with _ssh_connection(host_runner) as ssh:
                    out, err, code = ssh.exec(remote_md5_command(managed_path),
                                              timeout=3600)
                last_exc = None
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_exc = exc
        if last_exc is not None:
            return {"match": False, "expected": expected, "actual": "",
                    "location": f"runner {_runner_name(host_runner)}",
                    "error": f"ssh verify failed: "
                             f"{str(last_exc) or type(last_exc).__name__}"}
        if code != 0:
            return {"match": False, "expected": expected, "actual": "",
                    "location": "", "error": f"remote md5 failed: {err or out}"}
        actual = out.strip()
        return {"match": actual == expected, "expected": expected,
                "actual": actual, "location": f"runner {_runner_name(host_runner)}",
                "error": None}

    data_dir = os.path.join(imp_dir, "rawdata")
    if not os.path.isdir(data_dir):
        return {"match": False, "expected": expected, "actual": "",
                "location": "yuki storage",
                "error": f"no local data directory: {data_dir}"}
    from CelebiChrono.utils.file_utils import dir_md5
    actual = dir_md5(data_dir)
    return {"match": actual == expected, "expected": expected,
            "actual": actual, "location": "yuki storage", "error": None}


def synthesize_impression(project_uuid, impression_uuid, data_md5, descriptor,  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
                          runner_id, source_path, managed_dir,
                          status="running"):
    """Create the impression record in Yuki Storage (data stays remote).

    Replicates yuki_create_data's layout (contents/ + config.json +
    status.json) and adds remote.json marking the hosting runner.
    The status is "running" while the data copy is in flight; the caller
    flips it to "archived" (or "failed") when the copy settles.
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

    set_impression_status(project_uuid, impression_uuid, status)

    remote_file = metadata.ConfigFile(os.path.join(impression_dir, "remote.json"))
    remote_file.write_variable("host_runner_id", runner_id)
    remote_file.write_variable("source_path", source_path)
    remote_file.write_variable("remote_path", managed_dir)


def set_impression_status(project_uuid, impression_uuid, status):
    """Write the impression's status.json."""
    from CelebiChrono.utils import metadata
    status_file = metadata.ConfigFile(os.path.join(
        _yuki_dir(), "Storage", project_uuid, impression_uuid, "status.json"))
    status_file.write_variable("status", status)


def register_remote_data_job(runner_id, remote_path, project_uuid, descriptor,  # pylint: disable=too-many-locals
                             update):
    """Run the registration pipeline: hash -> copy -> register.

    ``update(state: dict)`` is called with each progress state.
    """
    from CelebiChrono.kernel.vimpression import VImpression
    from Yuki.cli.yuki_create_data import create_canonical_rawdata_task

    if not hasattr(VImpression, "generate_imp_uuid"):
        raise RuntimeError(
            "installed CelebiChrono is too old (missing "
            "VImpression.generate_imp_uuid); upgrade celebichrono or mount "
            "a local checkout via CELEBI_DIR")

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
            # Skip VImpression.__init__: it requires a Celebi project
            # context (csys.project_path()), which the celery worker does
            # not have. generate_imp_uuid uses no instance state.
            impression_uuid = VImpression.__new__(VImpression).generate_imp_uuid(
                project_uuid, tmp, [])

        settings = _ssh_settings(runner_id)
        managed_dir = (f"{settings.get('remote_workdir', '/tmp/yuki-workflows')}"
                       f"/impressions/{project_uuid}/{impression_uuid}")

        # The impression exists from here on: "running" while the copy is
        # in flight, so status queries gate downstream submits correctly.
        synthesize_impression(project_uuid, impression_uuid, data_md5,
                              descriptor, runner_id, remote_path, managed_dir,
                              status="running")

        update({"status": "copying", "result": None, "error": None})
        out, err, code = ssh.exec(
            build_remote_fast_copy_command(remote_path, managed_dir),
            timeout=10800)
        if code != 0:
            set_impression_status(project_uuid, impression_uuid, "failed")
            raise RuntimeError(f"remote copy failed: {err or out}")

        set_impression_status(project_uuid, impression_uuid, "archived")
        return {"uuid": data_md5, "impression_uuid": impression_uuid,
                "descriptor": descriptor}
