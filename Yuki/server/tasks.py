"""
Celery tasks for Yuki server.
"""
import os
from celery import Celery
from CelebiChrono.utils import metadata
from ..kernel import remote_data_ops
from ..kernel.vjob import VJob
from ..kernel.vworkflow import VWorkflow


def create_celery_app():
    """Create and configure Celery application."""
    app = Celery('yuki-server', broker='amqp://localhost')
    app.conf.update(
        result_backend='rpc://',
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
    )
    return app


# Create celery app instance
celeryapp = create_celery_app()


@celeryapp.task
def task_exec_impression(project_uuid, impressions, machine_uuid):
    """Execute impressions as a background task."""
    jobs = []
    for impression_uuid in impressions.split(" "):
        job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", project_uuid, impression_uuid)
        job = VJob(job_path, machine_uuid)
        jobs.append(job)
    print("jobs", jobs)
    config = metadata.ConfigFile(os.path.join(os.environ["HOME"], ".Yuki/config.json"))
    backend_types = config.read_variable("backend_types", {})
    backend_type = backend_types.get(machine_uuid, "reana")
    workflow = VWorkflow.create(project_uuid, jobs, None, mode=backend_type)
    print("workflow", workflow)

    # Validate runner binding for remote-hosted data impressions.
    runners_id = config.read_variable("runners_id", {})

    def _remote_host(impression):
        marker = os.path.join(os.environ["HOME"], ".Yuki", "Storage",
                              project_uuid, impression, "remote.json")
        if not os.path.exists(marker):
            return None
        return metadata.ConfigFile(marker).read_variable("host_runner_id", "")

    violations = []
    for job in workflow.jobs:
        if not job.is_input:
            continue
        impression = job.path.split("/")[-1] if job.path else ""
        host = _remote_host(impression) if impression else None
        if host and host != machine_uuid:
            violations.append((impression, host))

    if violations:
        from ..kernel.status_constants import DISSONANCE
        impression, host = violations[0]
        host_name = {v: k for k, v in runners_id.items()}.get(host, host)
        message = (f"Data impression {impression} is hosted on runner "
                   f"{host_name}. Submit this workflow to {host_name}, "
                   "or move the data via collect (coming later).")
        workflow.set_workflow_status("failed")
        for job in workflow.jobs:
            if not job.is_input:
                continue
            if job.job_type() == "algorithm":
                continue
            job.set_status(DISSONANCE, message)
        return

    workflow.run()


@celeryapp.task
def task_update_workflow_status(project_uuid, workflow_id):
    """Update workflow status as a background task."""
    print("# >>> task_update_workflow_status")
    workflow = VWorkflow.create(project_uuid, [], workflow_id)
    workflow.update_workflow_status()
    print("# <<< task_update_workflow_status")


@celeryapp.task
def task_register_remote_data(job_id, runner_id, remote_path, project_uuid,
                              descriptor):
    """Register remote data on an ssh runner: hash, copy, register."""
    yuki_dir = remote_data_ops._yuki_dir()

    def update(state):
        remote_data_ops.write_job_state(yuki_dir, job_id, state)

    try:
        result = remote_data_ops.register_remote_data_job(
            runner_id, remote_path, project_uuid, descriptor, update)
        update({"status": "done", "result": result, "error": None})
    except Exception as e:  # pylint: disable=broad-exception-caught
        update({"status": "failed", "result": None,
                "error": str(e) or type(e).__name__})
