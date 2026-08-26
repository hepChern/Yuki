"""Tests for impression storage selection helpers."""
# pylint: disable=protected-access
import json
import os
from unittest import mock

from Yuki.kernel.status_constants import CODA


def _storage(tmp_path):
    from Yuki.kernel import impression_storage as ims
    s = ims.ImpressionStorage.__new__(ims.ImpressionStorage)
    s.project_uuid = "proj-1"
    s.impression = "imp7"
    s.job_path = str(tmp_path / "job")
    s.runners = ["runner"]
    s.runners_id = {"runner": "runner-1"}
    return s, ims


def test_collect_light_downloads_plots_and_logs(tmp_path):
    """collect() pulls plots and logs for a finished job."""
    s, ims = _storage(tmp_path)
    job = mock.Mock()
    job.status.return_value = CODA
    wf = mock.Mock()
    wf.download_selected.return_value = {"collected": [], "skipped": [], "failed": []}
    wf.download_logs.return_value = {"collected": [], "skipped": [], "failed": []}
    s._get_runner_contexts = lambda: [("runner", job, wf)]
    report = s.collect()
    # download_selected called with the is_plot predicate on stageout
    assert wf.download_selected.call_args.args[1] is ims.file_types.is_plot
    wf.download_logs.assert_called_once()
    wf.download_outputs.assert_not_called()
    assert "runner" in report


def test_collect_files_uses_predicate(tmp_path):
    """collect_files filters downloads by the given predicate."""
    s, _ims = _storage(tmp_path)
    job = mock.Mock()
    job.status.return_value = CODA
    wf = mock.Mock()
    s._get_runner_contexts = lambda: [("runner", job, wf)]
    s.collect_files("stageout", "*.root")
    pred = wf.download_selected.call_args.args[1]
    assert pred("ntuple.root") and not pred("mass.png")


def test_file_status_merges(tmp_path):
    """file_status merges runner listings with local download state."""
    s, _ims = _storage(tmp_path)
    stageout = tmp_path / "job" / "runner-1" / "stageout"
    stageout.mkdir(parents=True)
    (stageout / "mass.png").write_bytes(b"img")        # downloaded
    wf = mock.Mock()
    wf.list_runner_files.return_value = [
        {"name": "mass.png", "size": 3},
        {"name": "ntuple.root", "size": 99},
    ]
    s._get_runner_contexts = lambda: [("runner", mock.Mock(), wf)]
    rows = {r["name"]: r for r in s.file_status("stageout")}
    assert rows["mass.png"]["in_yuki"] and rows["mass.png"]["type"] == "plot"
    assert rows["ntuple.root"]["in_runner"] and not rows["ntuple.root"]["in_yuki"]
    assert rows["ntuple.root"]["type"] == "data"


def _finished_job(wid="wf-1"):
    job = mock.Mock()
    job.status.return_value = CODA
    job.workflow_id.return_value = wid
    return job


def test_file_status_merges_nested_files(tmp_path):
    """file_status merges nested files from runner and local stageout."""
    s, _ims = _storage(tmp_path)
    stageout = tmp_path / "job" / "runner-1" / "stageout"
    stageout.mkdir(parents=True)
    (stageout / "mass.png").write_bytes(b"img")
    (stageout / "data").mkdir()
    (stageout / "data" / "ntuple.root").write_bytes(b"data")
    wf = mock.Mock()
    wf.list_runner_files.return_value = [
        {"name": "mass.png", "size": 3},
        {"name": "data/ntuple.root", "size": 4},
    ]
    s._get_runner_contexts = lambda: [("runner", mock.Mock(), wf)]
    rows = {r["name"]: r for r in s.file_status("stageout")}
    assert rows["mass.png"]["in_yuki"] and rows["mass.png"]["type"] == "plot"
    assert rows["data/ntuple.root"]["in_yuki"]
    assert rows["data/ntuple.root"]["type"] == "data"


def test_file_status_caches_runner_list_for_finished_job(tmp_path):
    """A finished job's runner listing is fetched live once, cached, and served
    from the cache on the next call without another REANA list_files."""
    s, _ims = _storage(tmp_path)
    wf = mock.Mock()
    wf.list_runner_files.return_value = [{"name": "mass.png", "size": 3}]
    s._get_runner_contexts = lambda: [("runner", _finished_job(), wf)]

    rows1 = {r["name"]: r for r in s.file_status("stageout")}
    rows2 = {r["name"]: r for r in s.file_status("stageout")}

    assert wf.list_runner_files.call_count == 1          # 2nd call served from cache
    assert rows1["mass.png"]["in_runner"] and rows2["mass.png"]["in_runner"]
    assert os.path.isfile(tmp_path / "job" / "runner-1" / "stageout.filelist.json")


def test_file_status_no_cache_while_running(tmp_path):
    """A running job is always listed live; its file set is still changing."""
    s, _ims = _storage(tmp_path)
    job = mock.Mock()
    job.status.return_value = "orchestrating"            # any non-CODA status
    job.workflow_id.return_value = "wf-1"
    wf = mock.Mock()
    wf.list_runner_files.return_value = [{"name": "partial.root", "size": 1}]
    s._get_runner_contexts = lambda: [("runner", job, wf)]

    s.file_status("stageout")
    s.file_status("stageout")

    assert wf.list_runner_files.call_count == 2
    assert not os.path.exists(tmp_path / "job" / "runner-1" / "stageout.filelist.json")


def test_file_status_does_not_cache_empty_listing(tmp_path):
    """A finished job whose live listing returns empty (e.g. a transient runner
    failure) is not cached, so the next call retries the runner."""
    s, _ims = _storage(tmp_path)
    wf = mock.Mock()
    wf.list_runner_files.return_value = []
    s._get_runner_contexts = lambda: [("runner", _finished_job(), wf)]

    s.file_status("stageout")
    s.file_status("stageout")

    assert wf.list_runner_files.call_count == 2
    assert not os.path.exists(tmp_path / "job" / "runner-1" / "stageout.filelist.json")


def test_file_status_cache_invalidated_on_new_workflow(tmp_path):
    """A cache written under one workflow id is ignored after a re-run."""
    s, _ims = _storage(tmp_path)
    wf = mock.Mock()
    wf.list_runner_files.return_value = [{"name": "old.png", "size": 1}]
    job = _finished_job("wf-1")
    s._get_runner_contexts = lambda: [("runner", job, wf)]
    s.file_status("stageout")                            # caches under wf-1

    wf.list_runner_files.return_value = [{"name": "new.png", "size": 2}]
    job.workflow_id.return_value = "wf-2"                # re-run -> new id
    rows = {r["name"]: r for r in s.file_status("stageout")}

    assert "new.png" in rows and "old.png" not in rows   # cache bypassed, re-listed
    assert wf.list_runner_files.call_count == 2


def _write_cache(machine_dir, wid, files):
    machine_dir.mkdir(parents=True, exist_ok=True)
    (machine_dir / "stageout.filelist.json").write_text(
        json.dumps({"workflow_id": wid, "files": files}))


def test_file_status_detailed_returns_files_and_notes(tmp_path):
    """file_status(detailed=True) returns {files, notes}; default stays a list."""
    s, _ims = _storage(tmp_path)
    wf = mock.Mock()
    wf.list_runner_files.return_value = []
    s._get_runner_contexts = lambda: [("runner", mock.Mock(), wf)]

    detail = s.file_status("stageout", detailed=True)

    assert isinstance(detail, dict)
    assert detail["files"] == []
    assert any("no stageout" in n["message"] for n in detail["notes"])
    assert s.file_status("stageout") == []               # default mode unchanged


def test_file_status_error_note_when_runner_unreachable(tmp_path):
    """A raising listing yields no files and an error note naming the failure."""
    s, _ims = _storage(tmp_path)
    wf = mock.Mock()
    wf.list_runner_files.side_effect = ConnectionError("boom")
    s._get_runner_contexts = lambda: [("runner", mock.Mock(), wf)]

    detail = s.file_status("stageout", detailed=True)

    assert detail["files"] == []
    notes = detail["notes"]
    assert any(n["level"] == "error" and "ConnectionError" in n["message"]
               and n["runner"] == "runner" for n in notes)


def test_file_status_falls_back_to_matching_cache_on_failure(tmp_path):
    """On live failure, a running re-run of the same workflow falls back to the
    cache its earlier finished phase wrote, with a warning note."""
    s, _ims = _storage(tmp_path)
    machine_dir = tmp_path / "job" / "runner-1"
    _write_cache(machine_dir, "wf-1", [{"name": "old.png", "size": 3}])
    job = mock.Mock()                        # running again, same workflow id
    job.status.return_value = "orchestrating"
    job.workflow_id.return_value = "wf-1"
    wf = mock.Mock()
    wf.list_runner_files.side_effect = ConnectionError("boom")
    s._get_runner_contexts = lambda: [("runner", job, wf)]

    detail = s.file_status("stageout", detailed=True)

    assert {r["name"] for r in detail["files"]} == {"old.png"}
    notes = detail["notes"]
    assert any(n["level"] == "warning" and "cached" in n["message"] for n in notes)


def test_file_status_ignores_mismatched_cache_on_failure(tmp_path):
    """A cache from another workflow run is not shown when the live listing
    fails (same-workflow-only policy)."""
    s, _ims = _storage(tmp_path)
    machine_dir = tmp_path / "job" / "runner-1"
    _write_cache(machine_dir, "wf-1", [{"name": "old.png", "size": 3}])
    wf = mock.Mock()
    wf.list_runner_files.side_effect = ConnectionError("boom")
    s._get_runner_contexts = lambda: [("runner", _finished_job("wf-2"), wf)]

    detail = s.file_status("stageout", detailed=True)

    assert detail["files"] == []
    assert any(n["level"] == "error" and "cached" not in n["message"]
               for n in detail["notes"])


def test_file_status_annotates_cache_hit_for_finished_job(tmp_path):
    """The cache-first path for a finished job carries an info note."""
    s, _ims = _storage(tmp_path)
    machine_dir = tmp_path / "job" / "runner-1"
    _write_cache(machine_dir, "wf-1", [{"name": "mass.png", "size": 3}])
    wf = mock.Mock()
    s._get_runner_contexts = lambda: [("runner", _finished_job("wf-1"), wf)]

    detail = s.file_status("stageout", detailed=True)

    assert not wf.list_runner_files.called               # served from cache
    notes = detail["notes"]
    assert any(n["level"] == "info" and "cached" in n["message"] for n in notes)
    assert {r["name"] for r in detail["files"]} == {"mass.png"}
