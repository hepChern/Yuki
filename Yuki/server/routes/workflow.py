"""
Workflow management routes for starting, stopping, and monitoring workflows.
"""
import os
from flask import Blueprint, request, jsonify
from Yuki.kernel.impression_storage import ImpressionStorage
from Yuki.kernel.vworkflow import VWorkflow
from Yuki.kernel.status_constants import IN_MOVEMENT, translate_to_musical

bp = Blueprint('workflow', __name__)

@bp.route("/kill/<project_uuid>/<impression>", methods=['GET'])
def kill(project_uuid, impression):
    """Kill a workflow for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    storage.kill()
    return "ok"

@bp.route("/collect/<project_uuid>/<impression>", methods=['GET'])
def collect(project_uuid, impression):
    """Collect workflow results for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    return jsonify(storage.collect())

@bp.route("/collect-outputs/<project_uuid>/<impression>", methods=['GET'])
def collect_outputs(project_uuid, impression):
    """Collect workflow outputs for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    return jsonify(storage.collect_outputs())

@bp.route("/collect-logs/<project_uuid>/<impression>", methods=['GET'])
def collect_logs(project_uuid, impression):
    """Collect workflow logs for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    return jsonify(storage.collect_logs())

@bp.route("/watermark/<project_uuid>/<impression>", methods=['GET'])
def watermark(project_uuid, impression):
    """Apply watermark to workflow results for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    storage.watermark()
    return "ok"

@bp.route('/workflow/<project_uuid>/<impression>', methods=['GET'])
def workflow(project_uuid, impression):
    """Get workflow information for a specific project and impression."""
    storage = ImpressionStorage(project_uuid, impression)
    return storage.get_info()


@bp.route("/collect-files/<project_uuid>/<impression>", methods=['GET'])
def collect_files(project_uuid, impression):
    """Collect a subset of files matching a selection spec.

    Query: kind (default stageout); one of type / pattern / names.
    """
    kind = request.args.get("kind", "stageout")
    if request.args.get("type"):
        spec = request.args.get("type")
    elif request.args.get("pattern"):
        spec = request.args.get("pattern")
    elif request.args.get("names"):
        spec = request.args.get("names")   # comma list handled below
    else:
        spec = "all"
    storage = ImpressionStorage(project_uuid, impression)
    report = {}
    if request.args.get("names"):
        for one in request.args.get("names").split(","):
            if one:
                report[one] = storage.collect_files(kind, one)
    else:
        report[spec] = storage.collect_files(kind, spec)
    return jsonify(report)


@bp.route("/delete-workflow/<project_uuid>/<workflow_uuid>", methods=['GET'])
def delete_workflow(project_uuid, workflow_uuid):
    """Free the runner-side workspace of a workflow (all backends).

    The local Workflows mirror is always kept; only the runner-side
    workspace is removed.
    """
    workflow_dir = os.path.join(os.environ["HOME"], ".Yuki", "Workflows",
                                project_uuid, workflow_uuid)
    if not os.path.isdir(workflow_dir):
        return jsonify({"error": f"workflow '{workflow_uuid}' not found"}), 404

    workflow = VWorkflow.create(project_uuid, [], workflow_uuid)
    if translate_to_musical(workflow.status()) == IN_MOVEMENT:
        return jsonify({"error": "workflow is running; kill it first"}), 409
    try:
        workflow.delete_workspace()
    except Exception as e:  # pylint: disable=broad-exception-caught
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "deleted",
                    "project_uuid": project_uuid,
                    "workflow": workflow_uuid,
                    "backend_type": workflow.backend_type()})
