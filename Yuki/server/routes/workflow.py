"""
Workflow management routes for starting, stopping, and monitoring workflows.
"""
from flask import Blueprint
from Yuki.kernel.impression_storage import ImpressionStorage

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
    storage.collect()
    return "ok"

@bp.route("/collect-outputs/<project_uuid>/<impression>", methods=['GET'])
def collect_outputs(project_uuid, impression):
    """Collect workflow outputs for a specific project and impression."""

    storage = ImpressionStorage(project_uuid, impression)
    storage.collect_outputs()
    return "ok"

@bp.route("/collect-logs/<project_uuid>/<impression>", methods=['GET'])
def collect_logs(project_uuid, impression):
    """Collect workflow logs for a specific project and impression."""

    storage = ImpressionStorage(project_uuid, impression)
    storage.collect_logs()
    return "ok"

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
