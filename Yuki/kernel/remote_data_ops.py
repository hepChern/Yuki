"""Remote-side data operations for register-data.

Helpers that build shell commands executed ON an ssh runner, plus the
local Yuki-side storage paths for registration job state.
"""
import os
import shlex

REMOTE_MD5_SCRIPT = r'''
import hashlib, os, sys

def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def dir_md5(root):
    total = hashlib.md5()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        files = [f for f in files if not f.startswith(".")]
        dirs.sort()
        files.sort()
        for name in files:
            total.update(md5sum(os.path.join(cur, name)).encode("utf-8"))
    return total.hexdigest()

print(dir_md5(sys.argv[1]))
'''


def _yuki_dir():
    """Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def remote_md5_command(remote_path):
    """SSH command computing the dir md5 on the remote host."""
    return f"python3 -c {shlex.quote(REMOTE_MD5_SCRIPT)} {shlex.quote(remote_path)}"


def build_remote_fast_copy_command(src, dst):
    """Copy src into dst on the remote host, fastest mechanism first.

    Mirrors yuki_create_data.fast_copy_tree: reflink -> hardlink ->
    rsync -> plain copy.
    """
    return (
        f"mkdir -p {shlex.quote(dst)} && "
        f"(cp -a --reflink=auto {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"cp -al {shlex.quote(src)}/. {shlex.quote(dst)}/ || "
        f"rsync -a {shlex.quote(src)}/ {shlex.quote(dst)}/ || "
        f"cp -r {shlex.quote(src)}/. {shlex.quote(dst)}/)"
    )
