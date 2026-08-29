"""Purge non-live workflow workspaces from a runner."""
import os

from CelebiChrono.utils.metadata import ConfigFile

from . import liveness
from .status_constants import IN_MOVEMENT, translate_to_musical
from .vworkflow import VWorkflow


def purge_stale_workflows(runner_id, dry_run=False, yuki_dir=None):
    """Delete the runner-side workspaces of workflows whose projects'
    synced live sets exclude them.

    Workflows are found in the local mirror
    ~/.Yuki/Workflows/<project>/<workflow> where config.json machine_id
    equals runner_id (covers ssh, native, and reana uniformly). Live
    workflows, running workflows, and workflows without an explicitly
    synced set are skipped with a reason. The mirror is always kept.

    Returns {"purged": [...], "skipped": [...], "dry_run": bool}.
    """
    yuki_dir = yuki_dir or liveness._yuki_dir()  # pylint: disable=protected-access
    workflows_root = os.path.join(yuki_dir, "Workflows")
    purged, skipped = [], []
    if not os.path.isdir(workflows_root):
        return {"purged": purged, "skipped": skipped,
                "dry_run": bool(dry_run)}

    for project in sorted(os.listdir(workflows_root)):
        project_dir = os.path.join(workflows_root, project)
        if not os.path.isdir(project_dir):
            continue
        for workflow_uuid in sorted(os.listdir(project_dir)):
            workflow_dir = os.path.join(project_dir, workflow_uuid)
            if not os.path.isdir(workflow_dir):
                continue
            workflow_config = ConfigFile(
                os.path.join(workflow_dir, "config.json"))
            if workflow_config.read_variable("machine_id", "") != runner_id:
                continue
            entry = {"project": project, "workflow": workflow_uuid}
            live = liveness.workflow_live(project, workflow_uuid, yuki_dir)
            if live is True:
                skipped.append({**entry, "reason": "workflow is live"})
                continue
            if live is None:
                skipped.append({**entry,
                                "reason": "no live set synced for project"})
                continue
            workflow = VWorkflow.create(project, [], workflow_uuid)
            if translate_to_musical(workflow.status()) == IN_MOVEMENT:
                skipped.append({**entry,
                                "reason": "workflow is running"})
                continue
            if dry_run:
                purged.append(entry)
                continue
            try:
                workflow.delete_workspace()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                skipped.append({**entry,
                                "reason": f"delete failed: {exc}"})
                continue
            purged.append(entry)
    return {"purged": purged, "skipped": skipped,
            "dry_run": bool(dry_run)}
