"""Result transfer logic for celebi-cli transfer."""
import fnmatch
import json
import os
from typing import List, Optional, Tuple

from Yuki.kernel import runner_config
from Yuki.kernel.ssh_workflow import _SshConnection


def _resolve_yuki_dir():
    """Return the Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def _parse_location(location: str) -> Tuple[str, Optional[str]]:
    """Parse 'yuki' or 'runner:<runner-id>' into (kind, runner_id)."""
    if location == "yuki":
        return "yuki", None
    if location.startswith("runner:"):
        runner_id = location[len("runner:"):]
        if not runner_id:
            raise ValueError("runner id is empty")
        return "runner", runner_id
    raise ValueError(f"invalid location: {location}")


def _list_local_files(root: str, pattern: Optional[str] = None) -> List[dict]:
    """List files under root as [{'name': rel_path, 'size': bytes}]."""
    result = []
    if not os.path.isdir(root):
        return result
    for dirpath, _dirs, filenames in os.walk(root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            result.append({"name": rel, "size": os.path.getsize(full)})
    return result


def _make_progress_dir(yuki_dir: str) -> str:
    """Create and return the transfer progress directory."""
    path = os.path.join(yuki_dir, "transfer-progress")
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_path(location: str, project_uuid: str, impression: str,
                  yuki_dir: str = None) -> Tuple[str, Optional[str]]:
    """Return (path, runner_id) for a location.

    For yuki: ~/.Yuki/Storage/<project>/<impression>/stageout
    For runner: <remote_workdir>/impressions/<project>/<impression>
    """
    kind, runner_name = _parse_location(location)
    if kind == "yuki":
        yuki_dir = yuki_dir or _resolve_yuki_dir()
        return os.path.join(yuki_dir, "Storage", project_uuid,
                            impression, "stageout"), None

    # runner
    yuki_dir = yuki_dir or _resolve_yuki_dir()
    config_file = runner_config.open_config()
    runners_id = config_file.read_variable("runners_id", {})
    if runner_name not in runners_id:
        raise ValueError(f"runner '{runner_name}' not found")
    runner_id = runners_id[runner_name]
    settings = runner_config.get_ssh_settings(config_file, runner_id)
    remote_workdir = settings.get("remote_workdir", "/tmp/yuki-workflows")
    remote_path = os.path.join(
        remote_workdir, "impressions", project_uuid, impression
    ).replace(os.sep, "/")
    return remote_path, runner_id


def _ssh_connection(runner_id: str) -> _SshConnection:
    """Build an SSH connection for runner_id from config."""
    config_file = runner_config.open_config()
    settings = runner_config.get_ssh_settings(config_file, runner_id)
    return _SshConnection(
        host=settings.get("host", ""),
        user=settings.get("user", ""),
        key_path=settings.get("key_path"),
        port=settings.get("port", 22),
    )


def _list_remote_files(ssh: _SshConnection, remote_root: str,
                       pattern: Optional[str] = None) -> List[dict]:
    """List files on the remote host under remote_root."""
    result = []
    if not ssh.exists(remote_root):
        return result
    for rel, full_path, size in ssh.walk_files(remote_root):
        if pattern and not fnmatch.fnmatch(rel, pattern):
            continue
        result.append({"name": rel, "size": size, "remote_path": full_path})
    return result
