"""
Runner management routes.
"""
import os
from flask import Blueprint, request, jsonify
from CelebiChrono.utils import csys
from ..config import config
from ..utils import ping

bp = Blueprint('runner', __name__)


def _ssh_ping(host, user, key_path, port=22):
    """Test SSH connectivity to a runner.

    Returns a dict with status and an optional message.
    """
    try:
        import paramiko
    except ImportError:
        return {"status": "Failed", "message": "paramiko is not installed"}

    if not host or not user:
        return {"status": "Failed", "message": "Missing ssh_host or ssh_user"}

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 10,
            "banner_timeout": 10,
        }
        key_path = os.path.expanduser(key_path) if key_path else None
        if key_path and os.path.exists(key_path):
            connect_kwargs["key_filename"] = key_path
        client.connect(**connect_kwargs)
        return {"status": "Connected"}
    except Exception as e:
        return {"status": "Failed", "message": str(e)}
    finally:
        client.close()


@bp.route("/runners", methods=['GET'])
def runners():
    """Get list of available runners."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    return " ".join(runners_list)


@bp.route("/runners-url", methods=['GET'])
def runnersurl():
    """Get URLs of all runners."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})
    runners_url = config_file.read_variable("urls", {})
    return " ".join([runners_url[runners_id[runner]] for runner in runners_list])


@bp.route("/runner-connection/<runner>", methods=['GET'])
def runnerconnection(runner):
    """Test connection to a specific runner."""
    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    runner_id = runners_id.get(runner, "")
    tokens = config_file.read_variable("tokens", {})
    token = tokens.get(runner_id, "")
    urls = config_file.read_variable("urls", {})
    url = urls.get(runner_id, "")
    backend_types = config_file.read_variable("backend_types", {})
    backend_type = backend_types.get(runner_id, "reana")

    if backend_type == "ssh":
        ssh_hosts = config_file.read_variable("ssh_hosts", {})
        ssh_users = config_file.read_variable("ssh_users", {})
        ssh_key_paths = config_file.read_variable("ssh_key_paths", {})
        ssh_ports = config_file.read_variable("ssh_ports", {})
        return _ssh_ping(
            host=ssh_hosts.get(runner_id, ""),
            user=ssh_users.get(runner_id, ""),
            key_path=ssh_key_paths.get(runner_id, ""),
            port=ssh_ports.get(runner_id, 22),
        )
    if backend_type != "reana":
        return {'status': 'Connected'}
    return ping(url, token)


@bp.route("/register-runner", methods=['POST'])
def registerrunner():
    """Register a new runner."""
    if request.method == 'POST':
        print(request.form)
        runner = request.form["runner"]
        runner_url = request.form["url"]
        runner_token = request.form["token"]
        backend_type = request.form.get("backend_type", "native")
        runner_id = csys.generate_uuid()

        config_file = config.get_config_file()
        runners_list = config_file.read_variable("runners", [])
        runners_id = config_file.read_variable("runners_id", {})
        runners_url = config_file.read_variable("urls", {})
        tokens = config_file.read_variable("tokens", {})
        backend_types = config_file.read_variable("backend_types", {})

        runners_list.append(runner)
        runners_id[runner] = runner_id
        runners_url[runner_id] = runner_url
        tokens[runner_id] = runner_token
        backend_types[runner_id] = backend_type

        config_file.write_variable("runners", runners_list)
        config_file.write_variable("runners_id", runners_id)
        config_file.write_variable("urls", runners_url)
        config_file.write_variable("tokens", tokens)
        config_file.write_variable("backend_types", backend_types)

        if backend_type == "ssh":
            _write_ssh_config(config_file, runner_id, request.form)

    return "successful"


def _write_ssh_config(config_file, runner_id, data):
    """Store SSH-specific runner settings in config.

    ``data`` may be a werkzeug MultiDict (request.form) or a plain dict.
    """
    ssh_hosts = config_file.read_variable("ssh_hosts", {})
    ssh_users = config_file.read_variable("ssh_users", {})
    ssh_key_paths = config_file.read_variable("ssh_key_paths", {})
    ssh_ports = config_file.read_variable("ssh_ports", {})
    remote_workdirs = config_file.read_variable("remote_workdirs", {})

    ssh_hosts[runner_id] = data.get("ssh_host", "")
    ssh_users[runner_id] = data.get("ssh_user", "")
    ssh_key_paths[runner_id] = data.get("ssh_key_path", "")
    try:
        ssh_ports[runner_id] = int(data.get("ssh_port", 22))
    except (ValueError, TypeError):
        ssh_ports[runner_id] = 22
    remote_workdirs[runner_id] = data.get("remote_workdir", "/tmp/yuki-workflows")

    config_file.write_variable("ssh_hosts", ssh_hosts)
    config_file.write_variable("ssh_users", ssh_users)
    config_file.write_variable("ssh_key_paths", ssh_key_paths)
    config_file.write_variable("ssh_ports", ssh_ports)
    config_file.write_variable("remote_workdirs", remote_workdirs)


def _remove_ssh_config(config_file, runner_id):
    """Remove SSH-specific settings for a runner."""
    for key in ("ssh_hosts", "ssh_users", "ssh_key_paths", "ssh_ports", "remote_workdirs"):
        data = config_file.read_variable(key, {})
        if runner_id in data:
            del data[runner_id]
            config_file.write_variable(key, data)


@bp.route("/remove-runner/<runner>", methods=['GET'])
def removerunner(runner):
    """Remove a runner."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})
    urls = config_file.read_variable("urls", {})
    tokens = config_file.read_variable("tokens", {})
    backend_types = config_file.read_variable("backend_types", {})

    if runner not in runners_list:
        return "runner not found"

    runner_id = runners_id[runner]
    backend_type = backend_types.get(runner_id, "reana")
    print("runner_id", runner_id)
    runners_list.remove(runner)
    del runners_id[runner]

    # Safe deletion of URL
    if runner_id in urls:
        del urls[runner_id]

    # Safe deletion of token
    if runner_id in tokens:
        del tokens[runner_id]

    # Safe deletion of backend type
    if runner_id in backend_types:
        del backend_types[runner_id]

    config_file.write_variable("runners", runners_list)
    config_file.write_variable("runners_id", runners_id)
    config_file.write_variable("urls", urls)
    config_file.write_variable("tokens", tokens)
    config_file.write_variable("backend_types", backend_types)

    if backend_type == "ssh":
        _remove_ssh_config(config_file, runner_id)

    return "successful"


@bp.route("/register-machine/<machine>/<machine_uuid>", methods=['GET'])
def register_machine(machine, machine_uuid):
    """Register a machine."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})
    runners_list.append(machine)
    runners_id[machine] = machine_uuid
    config_file.write_variable("runners", runners_list)
    config_file.write_variable("runners_id", runners_id)
    return "successful"


@bp.route("/update-runner/<runner>", methods=['PATCH'])
def update_runner(runner):
    """Update settings for an existing runner."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    if runner not in runners_list:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404

    runner_id = runners_id[runner]
    data = request.get_json(silent=True) or {}

    urls = config_file.read_variable("urls", {})
    tokens = config_file.read_variable("tokens", {})
    backend_types = config_file.read_variable("backend_types", {})
    use_kerberos = config_file.read_variable("use_kerberos", {})
    eos_mount_points = config_file.read_variable("eos_mount_point", {})
    cvmfs_repos = config_file.read_variable("cvmfs", {})

    old_backend_type = backend_types.get(runner_id, "reana")

    if "url" in data:
        urls[runner_id] = data["url"]
    if "token" in data:
        tokens[runner_id] = data["token"]
    if "backend_type" in data:
        backend_types[runner_id] = data["backend_type"]
    if "use_kerberos" in data:
        use_kerberos[runner_id] = data["use_kerberos"]
    if "eos_mount_point" in data:
        eos_mount_points[runner_id] = data["eos_mount_point"]
    if "cvmfs" in data:
        cvmfs_repos[runner_id] = data["cvmfs"]

    new_backend_type = backend_types.get(runner_id, "reana")

    config_file.write_variable("urls", urls)
    config_file.write_variable("tokens", tokens)
    config_file.write_variable("backend_types", backend_types)
    config_file.write_variable("use_kerberos", use_kerberos)
    config_file.write_variable("eos_mount_point", eos_mount_points)
    config_file.write_variable("cvmfs", cvmfs_repos)

    if new_backend_type == "ssh":
        _write_ssh_config(config_file, runner_id, data)
    elif old_backend_type == "ssh":
        _remove_ssh_config(config_file, runner_id)

    return jsonify({"message": f"Runner '{runner}' updated successfully"})


@bp.route("/runners-config", methods=['GET'])
def runners_config():
    """Get full configuration for all registered runners."""
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})
    urls = config_file.read_variable("urls", {})
    tokens = config_file.read_variable("tokens", {})
    backend_types = config_file.read_variable("backend_types", {})
    use_kerberos = config_file.read_variable("use_kerberos", {})
    eos_mount_points = config_file.read_variable("eos_mount_point", {})
    cvmfs_repos = config_file.read_variable("cvmfs", {})
    ssh_hosts = config_file.read_variable("ssh_hosts", {})
    ssh_users = config_file.read_variable("ssh_users", {})
    ssh_key_paths = config_file.read_variable("ssh_key_paths", {})
    ssh_ports = config_file.read_variable("ssh_ports", {})
    remote_workdirs = config_file.read_variable("remote_workdirs", {})

    result = []
    for runner in runners_list:
        runner_id = runners_id.get(runner, "")
        backend_type = backend_types.get(runner_id, "reana")
        runner_cfg = {
            "name": runner,
            "id": runner_id,
            "url": urls.get(runner_id, ""),
            "token": tokens.get(runner_id, ""),
            "backend_type": backend_type,
            "use_kerberos": use_kerberos.get(runner_id, False),
            "eos_mount_point": eos_mount_points.get(runner_id, ""),
            "cvmfs": cvmfs_repos.get(runner_id, []),
        }
        if backend_type == "ssh":
            runner_cfg.update({
                "ssh_host": ssh_hosts.get(runner_id, ""),
                "ssh_user": ssh_users.get(runner_id, ""),
                "ssh_key_path": ssh_key_paths.get(runner_id, ""),
                "ssh_port": ssh_ports.get(runner_id, 22),
                "remote_workdir": remote_workdirs.get(runner_id, "/tmp/yuki-workflows"),
            })
        result.append(runner_cfg)
    return jsonify(result)


@bp.route("/machine-id/<machine>", methods=["GET"])
def machine_id(machine):
    """Get machine ID for a specific machine."""
    config_file = config.get_config_file()
    runner_id = config_file.read_variable("runners_id", {})
    return runner_id[machine]
