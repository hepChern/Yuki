"""
Status and monitoring routes.
"""
import os
from flask import Blueprint, render_template
from Chern.utils.metadata import ConfigFile
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VWorkflow import VWorkflow
from ..config import config
from ..tasks import task_update_workflow_status

bp = Blueprint('status', __name__)


@bp.route('/setjobstatus/<impression>/<status>', methods=['GET'])
def setjobstatus(impression, status):
    """Set job status for an impression."""
    job_path = config.get_job_path(impression)
    job = VJob(job_path, None)
    job.set_status(status)
    return "ok"


@bp.route("/status/<impression>", methods=['GET'])
def status(impression):
    """Get status for an impression."""
    job_path = config.get_job_path(impression)
    config_file = config.get_config_file()
    runners = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    job_config_file = ConfigFile(config.get_job_config_path(impression))
    object_type = job_config_file.read_variable("object_type", "")

    if object_type == "":
        return "empty"

    for machine in runners:
        machine_id = runners_id[machine]

        job = VJob(job_path, machine_id)
        if job.workflow_id() == "":
            continue
        print("Checking status for job", job)
        workflow = VWorkflow([], job.workflow_id())
        status = workflow.status()
        print("Status from workflow", status)
        job.update_status_from_workflow(status)
        if status != "finished" and status != "failed":
            task_update_workflow_status.apply_async(args=[workflow.uuid])

        if status != "unknown":
            return status

        if os.path.exists(job_path):
            return "deposited"

    job = VJob(job_path, None)
    return job.status()


@bp.route("/status/<impression>/<machine>", methods=['GET'])
def runstatus(impression, machine):
    """Get run status for an impression on a specific machine."""
    job_path = config.get_job_path(impression)
    config_file = config.get_config_file()
    runners = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    job_config_file = ConfigFile(config.get_job_config_path(impression))
    object_type = job_config_file.read_variable("object_type", "")
    machine_id = runners_id[machine]

    if object_type == "":
        return "empty"

    job = VJob(job_path, machine_id)
    workflow = VWorkflow([], job.workflow_id())
    return workflow.status()


@bp.route("/deposited/<impression>", methods=['GET'])
def deposited(impression):
    """Check if an impression is deposited."""
    job_path = config.get_job_path(impression)
    if os.path.exists(job_path):
        return "TRUE"
    return "FALSE"


@bp.route("/ditestatus", methods=['GET'])
def ditestatus():
    """Get DITE status."""
    return "ok"


@bp.route("/samplestatus/<impression>", methods=['GET'])
def samplestatus(impression):
    """Get sample status for an impression."""
    job_config_file = ConfigFile(config.get_job_config_path(impression))
    return job_config_file.read_variable("sample_uuid", "")


@bp.route("/impression/<impression>", methods=['GET'])
def impression(impression):
    """Get impression path."""
    return config.get_job_path(impression)


@bp.route("/impview/<impression>", methods=['GET'])
def impview(impression):
    """View impression files."""
    job_path = config.get_job_path(impression)
    config_file = config.get_config_file()
    runners = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})
    runner_id = runners_id["local"]

    files = os.listdir(os.path.join(job_path, runner_id, "outputs"))
    file_infos = []
    for filename in files:
        ext = os.path.splitext(filename)[1].lower()
        is_image = ext in ('.png', '.jpg', '.jpeg', '.gif')
        file_infos.append({
            'name': filename,
            'is_image': is_image,
        })
    return render_template('impview.html', impression=impression, runner_id=runner_id, files=file_infos)
