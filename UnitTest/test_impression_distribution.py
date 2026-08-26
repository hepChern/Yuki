"""Tests for the impression distribution registry."""
# pylint: disable=protected-access
import json
import os
from unittest import mock

from Yuki.kernel import impression_storage as ims


def _storage(tmp_path):
    """Build an ImpressionStorage without touching the global config."""
    storage = ims.ImpressionStorage.__new__(ims.ImpressionStorage)
    storage.project_uuid = "proj-1"
    storage.impression = "imp7"
    storage.job_path = str(tmp_path / "job")
    storage.runners = ["cern"]
    storage.runners_id = {"cern": "runner-1"}
    return storage


def _read_dist(storage):
    """Read the persisted distribution registry."""
    path = os.path.join(storage.job_path, "distribution.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_dist(storage, dist):
    """Persist a distribution registry."""
    os.makedirs(storage.job_path, exist_ok=True)
    with open(os.path.join(storage.job_path, "distribution.json"),
              "w", encoding="utf-8") as fh:
        json.dump(dist, fh)


def test_update_distribution_computes_yuki_collected(tmp_path):
    """Local stageout files produce a yuki 'collected' entry."""
    storage = _storage(tmp_path)
    stageout = tmp_path / "job" / "runner-1" / "stageout"
    stageout.mkdir(parents=True)
    (stageout / "mass.png").write_bytes(b"img")  # 3 bytes
    storage._get_runner_contexts = lambda: []
    storage._remote_hosted_files = lambda kind: []

    storage.update_distribution()

    dist = _read_dist(storage)
    assert dist["produced_on"] is None
    assert dist["locations"]["yuki"]["origin"] == "collected"
    assert dist["locations"]["yuki"]["files"] == 1
    assert dist["locations"]["yuki"]["bytes"] == 3
    assert "updated" in dist["locations"]["yuki"]


def test_update_distribution_records_workflow_state(tmp_path):
    """Runner filelists produce produced_on and a 'workflow' state."""
    storage = _storage(tmp_path)
    job = mock.Mock()
    job.workflow_id.return_value = "wf-1"
    wf = mock.Mock()
    storage._get_runner_contexts = lambda: [("cern", job, wf)]
    storage._runner_files = lambda _j, _w, _k, _d: [
        {"name": "a.root", "size": 10},
        {"name": "mass.png", "size": 3},
    ]
    storage._remote_hosted_files = lambda kind: []

    storage.update_distribution()

    dist = _read_dist(storage)
    assert dist["produced_on"] == "cern"
    block = dist["locations"]["runner:cern"]
    assert block["workflow"]["origin"] == "produced"
    assert block["workflow"]["files"] == 2
    assert block["workflow"]["bytes"] == 13
    assert "cache" not in block


def test_update_distribution_records_registered_cache_state(tmp_path):
    """Remote-hosted (registered) data produces a 'cache' state."""
    storage = _storage(tmp_path)
    storage._get_runner_contexts = lambda: []
    storage._remote_hosted_files = lambda kind: [
        {"name": "dataset.root", "size": 99, "in_runner": True},
    ]
    # The host runner name is reverse-mapped from the remote marker.
    marker = tmp_path / "job" / "remote.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"host_runner_id": "runner-1"}))

    storage.update_distribution()

    dist = _read_dist(storage)
    block = dist["locations"]["runner:cern"]
    assert block["cache"]["origin"] == "registered"
    assert block["cache"]["files"] == 1
    assert block["cache"]["bytes"] == 99


def test_update_distribution_preserves_transferred_cache_entries(tmp_path):
    """Transferred cache entries survive a refresh and block recomputation."""
    storage = _storage(tmp_path)
    _write_dist(storage, {
        "produced_on": None,
        "locations": {
            "runner:pkufarm": {"cache": {"origin": "transferred",
                                         "files": 2, "bytes": 20,
                                         "updated": "t0"}},
            "yuki": {"origin": "transferred", "files": 2,
                     "bytes": 20, "updated": "t0"},
        },
    })
    stageout = tmp_path / "job" / "runner-1" / "stageout"
    stageout.mkdir(parents=True)
    (stageout / "mass.png").write_bytes(b"img")
    storage._get_runner_contexts = lambda: []
    storage._remote_hosted_files = lambda kind: []

    storage.update_distribution()

    dist = _read_dist(storage)
    assert dist["locations"]["runner:pkufarm"]["cache"]["origin"] == \
        "transferred"
    assert dist["locations"]["yuki"]["origin"] == "transferred"
    assert dist["locations"]["yuki"]["files"] == 2


def test_update_distribution_tracks_workflow_and_cache_states(tmp_path):
    """A runner with produced files and a cache shows both states."""
    storage = _storage(tmp_path)
    _write_dist(storage, {
        "produced_on": None,
        "locations": {
            "runner:cern": {"cache": {"origin": "transferred",
                                      "files": 1, "bytes": 99,
                                      "updated": "t0"}},
        },
    })
    job = mock.Mock()
    job.workflow_id.return_value = "wf-1"
    wf = mock.Mock()
    storage._get_runner_contexts = lambda: [("cern", job, wf)]
    storage._runner_files = lambda _j, _w, _k, _d: [
        {"name": "a.root", "size": 10},
    ]
    storage._remote_hosted_files = lambda kind: []

    storage.update_distribution()

    dist = _read_dist(storage)
    block = dist["locations"]["runner:cern"]
    assert block["workflow"]["origin"] == "produced"
    assert block["workflow"]["files"] == 1
    assert block["cache"]["origin"] == "transferred"
    assert block["cache"]["files"] == 1


def test_update_distribution_migrates_legacy_flat_entries(tmp_path):
    """Legacy flat runner entries are migrated to cache/workflow states."""
    storage = _storage(tmp_path)
    _write_dist(storage, {
        "produced_on": None,
        "locations": {
            "runner:pkufarm": {"origin": "transferred", "files": 2,
                               "bytes": 20, "updated": "t0"},
        },
    })
    storage._get_runner_contexts = lambda: []
    storage._remote_hosted_files = lambda kind: []

    storage.update_distribution()

    dist = _read_dist(storage)
    assert dist["locations"]["runner:pkufarm"]["cache"]["origin"] == \
        "transferred"
    assert dist["locations"]["runner:pkufarm"]["cache"]["files"] == 2
