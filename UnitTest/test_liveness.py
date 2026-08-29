"""Tests for the live-set registry (kernel/liveness.py)."""
import json
import os
from unittest import mock

from Yuki.kernel import liveness


def _write_run_config(tmp_path, project, impression, machine, workflow):
    """Write a per-machine run config with a workflow id."""
    run_dir = tmp_path / "Storage" / project / impression / machine
    run_dir.mkdir(parents=True)
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"workflow": workflow}, f)


def test_save_and_load_round_trip(monkeypatch, tmp_path):
    """save_live_set persists live, superseded, and derived workflows."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    _write_run_config(tmp_path, "proj", "a" * 32, "r1", "wf-1")
    _write_run_config(tmp_path, "proj", "b" * 32, "r1", "wf-2")

    summary = liveness.save_live_set("proj", ["a" * 32], ["c" * 32])

    assert summary == {"stored": True, "live": 1, "superseded": 1,
                       "live_workflows": 1}
    data = liveness.load_live_set("proj")
    assert data["live"] == ["a" * 32]
    assert data["superseded"] == ["c" * 32]
    assert data["live_workflows"] == ["wf-1"]
    assert "updated" in data
    # The file lives under .Yuki/Live/, not Bookkeep.
    assert os.path.isfile(
        tmp_path / "Live" / "proj.json")


def test_save_live_set_rejects_invalid_entries(monkeypatch, tmp_path):
    """Invalid uuids and uuids in both lists are rejected; nothing stored."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    with __import__("pytest").raises(ValueError) as exc:
        liveness.save_live_set("proj", ["not-a-uuid"], [])
    assert "not-a-uuid" in str(exc.value)
    assert not os.path.exists(tmp_path / "Live" / "proj.json")

    with __import__("pytest").raises(ValueError) as exc:
        liveness.save_live_set("proj", ["a" * 32], ["a" * 32])
    assert "both" in str(exc.value)
    assert not os.path.exists(tmp_path / "Live" / "proj.json")


def test_impression_live_semantics(monkeypatch, tmp_path):
    """Explicitly superseded -> False; everything else -> True; no set -> None."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.impression_live("proj", "a" * 32) is None
    liveness.save_live_set("proj", ["a" * 32], ["b" * 32])
    assert liveness.impression_live("proj", "a" * 32) is True
    assert liveness.impression_live("proj", "b" * 32) is False
    # Unknown-to-the-list is treated live (conservative).
    assert liveness.impression_live("proj", "c" * 32) is True


def test_workflow_live_semantics(monkeypatch, tmp_path):
    """Membership in derived live_workflows; None without a set."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.workflow_live("proj", "wf-1") is None
    _write_run_config(tmp_path, "proj", "a" * 32, "r1", "wf-1")
    liveness.save_live_set("proj", ["a" * 32], [])
    assert liveness.workflow_live("proj", "wf-1") is True
    assert liveness.workflow_live("proj", "wf-9") is False


def test_load_live_set_missing_or_corrupt(monkeypatch, tmp_path):
    """A missing or corrupt file loads as None."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.load_live_set("proj") is None
    live_dir = tmp_path / "Live"
    live_dir.mkdir()
    with open(live_dir / "proj.json", "w", encoding="utf-8") as f:
        f.write("{not json")
    assert liveness.load_live_set("proj") is None
