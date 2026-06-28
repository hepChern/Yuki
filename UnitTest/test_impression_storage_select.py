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
    s, ims = _storage(tmp_path)
    job = mock.Mock(); job.status.return_value = CODA
    wf = mock.Mock()
    s._get_runner_contexts = lambda: [("runner", job, wf)]
    s.collect()
    # download_selected called with the is_plot predicate on stageout
    assert wf.download_selected.call_args.args[1] is ims.file_types.is_plot
    wf.download_logs.assert_called_once()
    wf.download_outputs.assert_not_called()


def test_collect_files_uses_predicate(tmp_path):
    s, ims = _storage(tmp_path)
    job = mock.Mock()
    job.status.return_value = CODA
    wf = mock.Mock()
    s._get_runner_contexts = lambda: [("runner", job, wf)]
    s.collect_files("stageout", "*.root")
    pred = wf.download_selected.call_args.args[1]
    assert pred("ntuple.root") and not pred("mass.png")


def test_file_status_merges(tmp_path):
    s, ims = _storage(tmp_path)
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
