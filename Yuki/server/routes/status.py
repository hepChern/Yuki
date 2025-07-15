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


@bp.route('/setjobstatus/<impression_name>/<job_status>', methods=['GET'])
def setjobstatus(impression_name, job_status):
    """Set job status for an impression."""
    job_path = config.get_job_path(impression_name)
    job = VJob(job_path, None)
    job.set_status(job_status)
    return "ok"


@bp.route("/status/<impression_name>", methods=['GET'])
def status(impression_name):
    """Get status for an impression."""
    job_path = config.get_job_path(impression_name)
    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    job_config_file = ConfigFile(config.get_job_config_path(impression_name))
    object_type = job_config_file.read_variable("object_type", "")

    if object_type == "":
        return "empty"

    for machine in runners_list:
        machine_id = runners_id[machine]

        job = VJob(job_path, machine_id)
        if job.workflow_id() == "":
            continue
        print("Checking status for job", job)
        workflow = VWorkflow([], job.workflow_id())
        workflow_status = workflow.status()
        print("Status from workflow", workflow_status)
        job.update_status_from_workflow(workflow_status)
        if workflow_status not in ('finished', 'failed'):
            task_update_workflow_status.apply_async(args=[workflow.uuid])

        if workflow_status != "unknown":
            return workflow_status

        if os.path.exists(job_path):
            return "deposited"

    job = VJob(job_path, None)
    return job.status()


@bp.route("/status/<impression_name>/<machine>", methods=['GET'])
def runstatus(impression_name, machine):
    """Get run status for an impression on a specific machine."""
    job_path = config.get_job_path(impression_name)
    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})

    job_config_file = ConfigFile(config.get_job_config_path(impression_name))
    object_type = job_config_file.read_variable("object_type", "")
    machine_id = runners_id[machine]

    if object_type == "":
        return "empty"

    job = VJob(job_path, machine_id)
    workflow = VWorkflow([], job.workflow_id())
    return workflow.status()


@bp.route("/deposited/<impression_name>", methods=['GET'])
def deposited(impression_name):
    """Check if an impression is deposited."""
    job_path = config.get_job_path(impression_name)
    if os.path.exists(job_path):
        return "TRUE"
    return "FALSE"


@bp.route("/ditestatus", methods=['GET'])
def ditestatus():
    """Get DITE status."""
    return "ok"


@bp.route("/samplestatus/<impression_name>", methods=['GET'])
def samplestatus(impression_name):
    """Get sample status for an impression."""
    job_config_file = ConfigFile(config.get_job_config_path(impression_name))
    return job_config_file.read_variable("sample_uuid", "")


@bp.route("/impression/<impression_name>", methods=['GET'])
def impression(impression_name):
    """Get impression path."""
    return config.get_job_path(impression_name)


@bp.route("/impview/<impression_name>", methods=['GET'])
def impview(impression_name):
    """View impression files."""
    job_path = config.get_job_path(impression_name)
    config_file = config.get_config_file()
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
    return render_template('impview.html',
                          impression=impression_name,
                          runner_id=runner_id,
                          files=file_infos)
