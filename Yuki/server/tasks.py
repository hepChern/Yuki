"""
Celery tasks for Yuki server.
"""
import os
from celery import Celery
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VWorkflow import VWorkflow


def create_celery_app():
    """Create and configure Celery application."""
    celeryapp = Celery('yuki-server', broker='amqp://localhost')
    celeryapp.conf.update(
        result_backend='rpc://',
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
    )
    return celeryapp


# Create celery app instance
celeryapp = create_celery_app()


@celeryapp.task
def task_exec_impression(impressions, machine_uuid):
    """Execute impressions as a background task."""
    jobs = []
    for impression_uuid in impressions.split(" "):
        job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression_uuid)
        job = VJob(job_path, machine_uuid)
        jobs.append(job)
    print("jobs", jobs)
    workflow = VWorkflow(jobs, None)
    print("workflow", workflow)
    workflow.run()


@celeryapp.task
def task_update_workflow_status(workflow_id):
    """Update workflow status as a background task."""
    print("# >>> task_update_workflow_status")
    workflow = VWorkflow([], workflow_id)
    workflow.update_workflow_status()
    print("# <<< task_update_workflow_status")
