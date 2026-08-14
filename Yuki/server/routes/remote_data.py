"""
Remote data registration routes.

POST /register-remote-data            start a registration job (returns job_id)
GET  /register-remote-data/<job_id>   poll job state
"""
import os
from flask import Blueprint, request, jsonify
from CelebiChrono.utils import csys
from ...kernel import remote_data_ops
from ..config import config
from ..tasks import task_register_remote_data

bp = Blueprint('remote_data', __name__)


@bp.route("/register-remote-data", methods=['POST'])
def register_remote_data():
    """Start a remote data registration job."""
    data = request.get_json(silent=True) or request.form
    runner = data.get("runner", "")
    remote_path = data.get("remote_path", "")
    project_uuid = data.get("project_uuid", "")
    if not (runner and remote_path and project_uuid):
        return jsonify({"error": "missing required field: runner/remote_path/project_uuid"}), 400

    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    if runner not in runners_id:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404
    runner_id = runners_id[runner]
    backend_types = config_file.read_variable("backend_types", {})
    if backend_types.get(runner_id, "reana") != "ssh":
        return jsonify({"error": "register-data requires an ssh runner; "
                                 "native data should use upload-data"}), 400

    descriptor = data.get("descriptor") or os.path.basename(
        os.path.normpath(remote_path))

    yuki_dir = remote_data_ops._yuki_dir()
    existing = remote_data_ops.find_existing_registration(
        yuki_dir, runner_id, remote_path)
    if existing:
        return jsonify(existing)
    inflight = remote_data_ops.find_inflight_job(yuki_dir, runner_id, remote_path)
    if inflight:
        return jsonify({"job_id": inflight})

    job_id = csys.generate_uuid()
    remote_data_ops.write_job_state(yuki_dir, job_id, {
        "status": "hashing", "result": None, "error": None,
        "runner_id": runner_id, "remote_path": remote_path,
    })
    try:
        task_register_remote_data.apply_async(
            args=[job_id, runner_id, remote_path, project_uuid, descriptor])
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Record the failure so find_inflight_job never wedges on this job.
        remote_data_ops.write_job_state(yuki_dir, job_id, {
            "status": "failed", "result": None, "error": str(e),
            "runner_id": runner_id, "remote_path": remote_path,
        })
        return jsonify({"job_id": job_id, "error": str(e)}), 500
    return jsonify({"job_id": job_id})


@bp.route("/register-remote-data/<job_id>", methods=['GET'])
def register_remote_data_status(job_id):
    """Poll a registration job's state."""
    state = remote_data_ops.read_job_state(remote_data_ops._yuki_dir(), job_id)
    if state is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(state)
