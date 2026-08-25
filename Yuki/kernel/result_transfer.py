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


def _sanitize_rel_path(rel: str) -> str:
    """Validate a relative path and reject traversal/absolute paths."""
    if not rel:
        raise ValueError("empty relative path")
    if os.path.isabs(rel) or rel.startswith("/"):
        raise ValueError(f"absolute path not allowed: {rel}")
    parts = rel.replace(os.sep, "/").split("/")
    if ".." in parts:
        raise ValueError(f"path traversal not allowed: {rel}")
    return rel


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
            rel = _sanitize_rel_path(rel)
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
        rel = _sanitize_rel_path(rel)
        if pattern and not fnmatch.fnmatch(rel, pattern):
            continue
        result.append({"name": rel, "size": size, "remote_path": full_path})
    return result


# pylint: disable=too-many-arguments,too-many-positional-arguments
def _copy_local_to_local(src_root: str, dst_root: str, force: bool,
                         progress: dict, report: dict) -> None:
    """Copy files from src_root to dst_root."""
    files = _list_local_files(src_root)
    for entry in files:
        rel = entry["name"]
        progress["current_file"] = rel
        src_file = os.path.join(src_root, rel)
        dst_file = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        if os.path.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            with open(src_file, "rb") as sf, open(dst_file, "wb") as df:
                while True:
                    chunk = sf.read(65536)
                    if not chunk:
                        break
                    df.write(chunk)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


# pylint: disable=too-many-arguments,too-many-positional-arguments
def _copy_local_to_remote(src_root: str, dst_root: str, ssh: _SshConnection,
                          force: bool, progress: dict, report: dict) -> None:
    """Upload files from src_root to dst_root on the remote host."""
    files = _list_local_files(src_root)
    for entry in files:
        rel = entry["name"]
        progress["current_file"] = rel
        src_file = os.path.join(src_root, rel)
        dst_file = f"{dst_root}/{rel}"
        if ssh.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            ssh.put(src_file, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


# pylint: disable=too-many-arguments,too-many-positional-arguments
def _copy_remote_to_local(src_root: str, dst_root: str, ssh: _SshConnection,
                          force: bool, progress: dict, report: dict) -> None:
    """Download files from src_root on the remote host to dst_root."""
    files = _list_remote_files(ssh, src_root)
    for entry in files:
        rel = entry["name"]
        progress["current_file"] = rel
        src_file = entry["remote_path"]
        dst_file = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        if os.path.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        try:
            ssh.get(src_file, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def _copy_remote_to_remote(src_root: str, dst_root: str,
                           src_ssh: _SshConnection, dst_ssh: _SshConnection,
                           force: bool, progress: dict, report: dict) -> None:
    """Stream files from src runner to dst runner through Yuki host."""
    import tempfile
    files = _list_remote_files(src_ssh, src_root)
    for entry in files:
        rel = entry["name"]
        src_file = entry["remote_path"]
        dst_file = f"{dst_root}/{rel}"
        progress["current_file"] = rel
        if dst_ssh.exists(dst_file) and not force:
            report["skipped"].append(rel)
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
                for chunk in src_ssh.stream(src_file):
                    tmp.write(chunk)
            dst_ssh.put(tmp_path, dst_file)
            progress["bytes_done"] += entry["size"]
            report["transferred"].append(rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report["failed"].append({"file": rel, "reason": str(exc)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


def _report_counts(report: dict) -> dict:
    """Return top-level count keys plus the full report."""
    return {
        "transferred": len(report["transferred"]),
        "skipped": len(report["skipped"]),
        "failed": len(report["failed"]),
        "report": report,
    }


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def run_transfer(job_id: str, project_uuid: str, impression: str,
                 source: str, destination: str,
                 pattern: Optional[str] = None, force: bool = False,
                 yuki_dir: str = None) -> dict:
    """Run a transfer job and return the final report."""
    yuki_dir = yuki_dir or _resolve_yuki_dir()
    src_path, src_runner = _resolve_path(source, project_uuid, impression, yuki_dir)
    dst_path, dst_runner = _resolve_path(destination, project_uuid, impression, yuki_dir)

    if source == "yuki" and destination == "yuki":
        raise ValueError("source and destination cannot both be yuki")

    progress_dir = _make_progress_dir(yuki_dir)
    progress_path = os.path.join(progress_dir, f"{job_id}.json")

    progress = {"bytes_done": 0, "bytes_total": 0, "current_file": ""}

    def write_status(status, extra=None):
        state = {
            "status": status,
            "bytes_done": progress["bytes_done"],
            "bytes_total": progress["bytes_total"],
            "current_file": progress.get("current_file", ""),
        }
        if extra:
            state.update(extra)
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    report = {"transferred": [], "skipped": [], "failed": []}

    try:
        if source == "yuki" and destination.startswith("runner:"):
            with _ssh_connection(dst_runner) as dst_ssh:
                src_files = _list_local_files(src_path, pattern)
                progress["bytes_total"] = sum(f["size"] for f in src_files)
                write_status("running")
                _copy_local_to_remote(src_path, dst_path, dst_ssh, force, progress, report)
        elif source.startswith("runner:") and destination == "yuki":
            with _ssh_connection(src_runner) as src_ssh:
                src_files = _list_remote_files(src_ssh, src_path, pattern)
                progress["bytes_total"] = sum(f["size"] for f in src_files)
                write_status("running")
                _copy_remote_to_local(src_path, dst_path, src_ssh, force, progress, report)
        elif source.startswith("runner:") and destination.startswith("runner:"):
            with _ssh_connection(src_runner) as src_ssh, \
                 _ssh_connection(dst_runner) as dst_ssh:
                src_files = _list_remote_files(src_ssh, src_path, pattern)
                progress["bytes_total"] = sum(f["size"] for f in src_files)
                write_status("running")
                _copy_remote_to_remote(src_path, dst_path, src_ssh, dst_ssh,
                                       force, progress, report)
        write_status("done", _report_counts(report))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        write_status("failed", {**_report_counts(report), "error": str(exc)})
        raise

    return report
