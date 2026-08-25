"""Tests for Yuki.kernel.result_transfer."""
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
