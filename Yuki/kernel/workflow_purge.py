"""Purge non-live workflow workspaces from a runner."""
import os

from CelebiChrono.utils.metadata import ConfigFile

from . import liveness
from .status_constants import IN_MOVEMENT, translate_to_musical
from .vworkflow import VWorkflow


def _project_live_workflows(project, yuki_dir):
    """Fresh per-project live workflow uuids, or None without a synced set.

    Liveness is derived at purge time rather than read from the stored
    snapshot's live_workflows: a re-run of a live impression after the
    last sync points at a new workflow uuid the snapshot does not list.
    """
    live_set = liveness.load_live_set(project, yuki_dir)
    if live_set is None:
        return None
    return liveness.derive_live_workflows(
        project, live_set.get("live", []), yuki_dir)


def purge_stale_workflows(runner_id, dry_run=False, yuki_dir=None,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                           project_uuid=None):
    """Delete the runner-side workspaces of workflows whose projects'
    synced live sets exclude them.

    Workflows are found in the local mirror
    ~/.Yuki/Workflows/<project>/<workflow> where config.json machine_id
    equals runner_id (covers ssh, native, and reana uniformly). With
    project_uuid given, only that project's workflows are scanned. Live
    workflows are derived fresh at purge time from the projects' run
    configs (a re-run after the last sync points at a new workflow the
    stored snapshot does not list); running workflows and workflows
    without an explicitly synced set are skipped with a reason. A
    corrupt mirror entry is a per-workflow skip, never a sweep abort.
    The mirror is always kept.

    Returns {"purged": [...], "skipped": [...], "dry_run": bool}.
    """
    yuki_dir = yuki_dir or liveness._yuki_dir()  # pylint: disable=protected-access
    workflows_root = os.path.join(yuki_dir, "Workflows")
    purged, skipped = [], []
    if not os.path.isdir(workflows_root):
        return {"purged": purged, "skipped": skipped,
                "dry_run": bool(dry_run)}

    projects = [project_uuid] if project_uuid else sorted(
        os.listdir(workflows_root))
    for project in projects:
        project_dir = os.path.join(workflows_root, project)
        if not os.path.isdir(project_dir):
            continue
        # Cached per project for the whole sweep (fresh per invocation).
        live_workflows = _project_live_workflows(project, yuki_dir)
        for workflow_uuid in sorted(os.listdir(project_dir)):
            workflow_dir = os.path.join(project_dir, workflow_uuid)
            if not os.path.isdir(workflow_dir):
                continue
            entry = {"project": project, "workflow": workflow_uuid}
            try:
                workflow_config = ConfigFile(
                    os.path.join(workflow_dir, "config.json"))
                if workflow_config.read_variable("machine_id", "") != runner_id:
                    continue
                if live_workflows is None:
                    skipped.append({**entry,
                                    "reason": "no live set synced for project"})
                    continue
                if workflow_uuid in live_workflows:
                    skipped.append({**entry, "reason": "workflow is live"})
                    continue
                workflow = VWorkflow.create(project, [], workflow_uuid)
                if translate_to_musical(workflow.status()) == IN_MOVEMENT:
                    skipped.append({**entry,
                                    "reason": "workflow is running"})
                    continue
                if dry_run:
                    purged.append(entry)
                    continue
                workflow.delete_workspace()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                skipped.append({**entry, "reason": f"purge failed: {exc}"})
                continue
            purged.append(entry)
    return {"purged": purged, "skipped": skipped,
            "dry_run": bool(dry_run)}
