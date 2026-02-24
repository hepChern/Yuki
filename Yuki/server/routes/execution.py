"""
Job execution routes.
"""
from logging import getLogger

from flask import Blueprint, request

from ...kernel.vjob import VJob
from ...kernel.container_job import ContainerJob
from ..config import config
from ..tasks import task_exec_impression
import shutil  # pylint: disable=wrong-import-order
import json  # pylint: disable=wrong-import-order

bp = Blueprint('execution', __name__)
logger = getLogger("YukiLogger")

@bp.route('/execute', methods=['GET', 'POST'])
def execute():
    """Execute impressions."""
    if request.method == 'POST':
        machine = request.form["machine"]
        project_uuid = request.form['project_uuid']
        use_eos_dict = request.form["use_eos"]
        use_eos_dict = json.loads(use_eos_dict)
        contents = request.files["impressions"].read().decode()
        impressions = [item for item in contents.split(" ") if item]
        start_jobs = []

        for impression in impressions:
            job_path = config.get_job_path(project_uuid, impression)
            job = VJob(job_path, None)
            job_type = job.job_type()
            job_status = job.status()

            if job_type == "task":
                if job_status not in ("raw", "failed"):
                    continue
                job.set_status("waiting")
                start_job = VJob(job_path, machine)
                use_eos = use_eos_dict.get(impression, False)
                start_job.set_use_eos(use_eos)
                start_jobs.append(start_job)
            elif job_type == "algorithm":
                job.set_status("ready")

        if len(start_jobs) == 0:
            return "no job to run"

        contents = " ".join([job.uuid for job in start_jobs])
        task = task_exec_impression.apply_async(args=[project_uuid, contents, machine])

        for job in start_jobs:
            job.set_runid(task.id)
        return task.id

    return ""  # For GET requests

@bp.route('/purge', methods=['GET', 'POST'])
def purge():
    """Purge impressions."""
    print("# >>> purge")
    if request.method == 'POST':
        contents = request.files["impressions"].read().decode()
        project_uuid = request.form['project_uuid']
        print("contents:", contents.split(" "))

        for impression in contents.split(" "):
            print("impression:", impression)
            job_path = config.get_job_path(project_uuid, impression)
            # try to remove the job
            shutil.rmtree(job_path, ignore_errors=True)

        print("contents", contents)
        print("### <<< purge")
    return ""  # For GET requests




@bp.route("/run/<project_uuid>/<impression>/<machine>", methods=['GET'])
def run(project_uuid, impression, machine):
    """Run a specific impression on a machine."""
    logger.info("Trying to run it")
    task = task_exec_impression.apply_async(args=[project_uuid, impression, machine])
    job_path = config.get_job_path(project_uuid, impression)
    VJob(job_path, machine).set_runid(task.id)
    logger.info("Run id = %s", task.id)
    return task.id


@bp.route("/outputs/<project_uuid>/<impression>/<machine>", methods=['GET'])
def outputs(project_uuid, impression, machine):
    """Get outputs for an impression on a specific machine."""
    if machine == "none":
        path = config.get_job_path(project_uuid, impression)
        job = VJob(path, None)
        if job.job_type() == "task":
            return " ".join(ContainerJob(path, None).outputs())

    path = config.get_job_path(project_uuid, impression)
    job = VJob(path, machine)
    if job.job_type() == "task":
        return " ".join(ContainerJob(path, machine).outputs())
    return ""
