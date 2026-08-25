"""Tests for Yuki.kernel.result_transfer."""
import json
import os
from unittest import mock

import pytest

from Yuki.kernel import result_transfer


def test_resolve_yuki_dir_defaults_to_home():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert result_transfer._resolve_yuki_dir() == os.path.expanduser("~/.Yuki")


def test_resolve_yuki_dir_uses_yukidir_env():
    with mock.patch.dict(os.environ, {"YUKIDIR": "/tmp/yuki-test"}):
        assert result_transfer._resolve_yuki_dir() == "/tmp/yuki-test"


def test_parse_location_yuki():
    assert result_transfer._parse_location("yuki") == ("yuki", None)


def test_parse_location_runner():
    assert result_transfer._parse_location("runner:pkufarm") == ("runner", "pkufarm")


def test_parse_location_runner_missing_id():
    with pytest.raises(ValueError):
        result_transfer._parse_location("runner:")


def test_list_local_files(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    files = result_transfer._list_local_files(str(tmp_path))
    assert sorted(f["name"] for f in files) == ["a.txt", "sub/b.txt"]
    assert files[0]["size"] == 5


def test_list_local_files_missing_dir():
    files = result_transfer._list_local_files("/nonexistent/path")
    assert files == []


def test_resolve_path_yuki(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    project_uuid = "proj-uuid"
    impression = "imp-uuid"
    path, runner_id = result_transfer._resolve_path("yuki", project_uuid, impression)
    assert path == str(yuki_dir / "Storage" / project_uuid / impression / "stageout")
    assert runner_id is None


def test_resolve_path_runner(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    config_path = yuki_dir / "config.json"
    config_path.write_text(json.dumps({
        "runners_id": {"pkufarm": "runner-uuid"},
        "runners": ["pkufarm"],
        "runner_settings": {
            "runner-uuid": {"ssh_host": "host", "ssh_user": "user",
                            "remote_workdir": "/remote/work"}
        }
    }))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    path, runner_id = result_transfer._resolve_path(
        "runner:pkufarm", "proj-uuid", "imp-uuid")
    assert path == "/remote/work/impressions/proj-uuid/imp-uuid"
    assert runner_id == "runner-uuid"


def test_list_remote_files_uses_ssh_walk():
    ssh = mock.MagicMock()
    ssh.walk_files.return_value = [
        ("a.txt", "/remote/a.txt", 10),
        ("sub/b.txt", "/remote/sub/b.txt", 20),
    ]
    files = result_transfer._list_remote_files(ssh, "/remote/root", "*.txt")
    assert sorted(f["name"] for f in files) == ["a.txt", "sub/b.txt"]


def test_copy_local_to_local(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("world")
    progress = {"bytes_done": 0}
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_local(
        str(src), str(dst), force=False,
        progress=progress, report=report)
    assert sorted(report["transferred"]) == ["a.txt", "sub/b.txt"]
    assert (dst / "a.txt").read_text() == "hello"
    assert (dst / "sub" / "b.txt").read_text() == "world"
    assert progress["bytes_done"] == 10


def test_copy_local_to_local_skips_existing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("hello")
    (dst / "a.txt").write_text("existing")
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_local(
        str(src), str(dst), force=False,
        progress={"bytes_done": 0}, report=report)
    assert report["skipped"] == ["a.txt"]
    assert (dst / "a.txt").read_text() == "existing"


def test_copy_local_to_remote(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    ssh = mock.MagicMock()
    ssh.exists.return_value = False
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_local_to_remote(
        str(src), "/remote/dst", ssh, force=False,
        progress={"bytes_done": 0}, report=report)
    ssh.put.assert_called_once()
    assert report["transferred"] == ["a.txt"]


def test_copy_remote_to_local(tmp_path):
    dst = tmp_path / "dst"
    ssh = mock.MagicMock()
    ssh.exists.return_value = True
    ssh.walk_files.return_value = [("a.txt", "/remote/a.txt", 5)]
    report = {"transferred": [], "skipped": [], "failed": []}
    result_transfer._copy_remote_to_local(
        "/remote/src", str(dst), ssh, force=False,
        progress={"bytes_done": 0}, report=report)
    ssh.get.assert_called_once_with("/remote/a.txt", mock.ANY)
    assert report["transferred"] == ["a.txt"]
