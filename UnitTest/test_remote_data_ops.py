"""Tests for remote data operation helpers."""
import os
import subprocess
from unittest import mock

from CelebiChrono.utils.file_utils import dir_md5
from Yuki.kernel.remote_data_ops import (
    REMOTE_MD5_SCRIPT, remote_md5_command, build_remote_fast_copy_command,
    _yuki_dir,
)


def _fixture_tree(root):
    """Nested tree with hidden files, matching dir_md5's exclude rules."""
    os.makedirs(os.path.join(root, "sub", "deep"))
    os.makedirs(os.path.join(root, ".hidden_dir"))
    with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as f:
        f.write("alpha")
    with open(os.path.join(root, "sub", "b.txt"), "w", encoding="utf-8") as f:
        f.write("beta")
    with open(os.path.join(root, "sub", "deep", "c.txt"), "w", encoding="utf-8") as f:
        f.write("gamma")
    with open(os.path.join(root, ".secret"), "w", encoding="utf-8") as f:
        f.write("hidden file content")
    with open(os.path.join(root, ".hidden_dir", "d.txt"), "w", encoding="utf-8") as f:
        f.write("hidden dir content")


def test_remote_md5_matches_dir_md5_semantics(tmp_path):
    """The remote md5 script agrees with dir_md5 on the same tree."""
    fixture = tmp_path / "data"
    _fixture_tree(str(fixture))
    expected = dir_md5(str(fixture))

    result = subprocess.run(
        ["python3", "-c", REMOTE_MD5_SCRIPT, str(fixture)],
        capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_remote_md5_command_quotes_args():
    """remote_md5_command quotes paths containing spaces."""
    cmd = remote_md5_command("/data/my dir/with spaces")
    assert cmd.startswith("python3 -c ")
    assert "'/data/my dir/with spaces'" in cmd


def test_fast_copy_command_chain():
    """The fast-copy command falls back through reflink/hardlink/rsync/cp."""
    cmd = build_remote_fast_copy_command("/src dir", "/dst dir")
    assert "mkdir -p '/dst dir'" in cmd
    assert "cp -a --reflink=auto '/src dir'/." in cmd
    assert "cp -al '/src dir'/." in cmd
    assert "rsync -a '/src dir'/" in cmd
    assert "cp -r '/src dir'/." in cmd


def test_yuki_dir_env(monkeypatch, tmp_path):
    """_yuki_dir honors YUKIDIR and falls back to HOME/.Yuki."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path / "custom"))
    assert _yuki_dir() == str(tmp_path / "custom")
    monkeypatch.delenv("YUKIDIR")
    with mock.patch.dict(os.environ, {"HOME": str(tmp_path)}):
        assert _yuki_dir() == os.path.join(str(tmp_path), ".Yuki")
