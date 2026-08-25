"""Tests for Yuki.kernel.result_transfer."""
# pylint: disable=missing-function-docstring
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
    size_map = {f["name"]: f["size"] for f in files}
    assert size_map["a.txt"] == 5


def test_list_local_files_missing_dir():
    files = result_transfer._list_local_files("/nonexistent/path")
    assert not files


def test_list_local_files_rejects_traversal(tmp_path, monkeypatch):
    def bad_walk(_root):
        yield str(tmp_path), [], ["../etc/passwd"]
    monkeypatch.setattr(result_transfer.os, "walk", bad_walk)
    with pytest.raises(ValueError, match="path traversal not allowed"):
        result_transfer._list_local_files(str(tmp_path))


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


def test_list_remote_files_rejects_traversal():
    ssh = mock.MagicMock()
    ssh.exists.return_value = True
    ssh.walk_files.return_value = [
        ("../etc/passwd", "/remote/../etc/passwd", 10),
    ]
    with pytest.raises(ValueError, match="path traversal not allowed"):
        result_transfer._list_remote_files(ssh, "/remote/root")


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


def test_run_transfer_yuki_to_yuki_rejected(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    with pytest.raises(ValueError, match="source and destination cannot both be yuki"):
        result_transfer.run_transfer(
            "job1", "proj", "imp", "yuki", "yuki",
            pattern=None, force=False, yuki_dir=str(yuki_dir))


def test_run_transfer_with_mocked_remote(tmp_path, monkeypatch):
    yuki_dir = tmp_path / "yuki"
    yuki_dir.mkdir()
    storage = yuki_dir / "Storage" / "proj" / "imp" / "stageout"
    storage.mkdir(parents=True)
    (storage / "a.txt").write_text("hello")
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

    with mock.patch("Yuki.kernel.result_transfer._ssh_connection") as ssh_conn:
        ssh = mock.MagicMock()
        ssh.exists.return_value = False
        ssh.walk_files.return_value = []
        ssh_conn.return_value.__enter__ = mock.Mock(return_value=ssh)
        ssh_conn.return_value.__exit__ = mock.Mock(return_value=False)
        report = result_transfer.run_transfer(
            "job1", "proj", "imp", "yuki", "runner:pkufarm",
            pattern=None, force=False, yuki_dir=str(yuki_dir))
        assert report["transferred"] == ["a.txt"]
        ssh.put.assert_called_once()

        progress_path = yuki_dir / "transfer-progress" / "job1.json"
        progress = json.loads(progress_path.read_text())
        assert progress["status"] == "done"
        assert progress["transferred"] == 1
        assert progress["skipped"] == 0
        assert progress["failed"] == 0
        assert progress["bytes_total"] == 5
        assert progress["report"]["transferred"] == ["a.txt"]
