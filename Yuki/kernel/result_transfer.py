"""Result transfer logic for celebi-cli transfer."""
import fnmatch
import os
from typing import List, Optional, Tuple


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
