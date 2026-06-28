import os
import tempfile
from unittest import mock

from Yuki.kernel import reana_workflow


def _make_wf():
    wf = reana_workflow.ReanaWorkflow.__new__(reana_workflow.ReanaWorkflow)
    wf.machine_id = "runner-1"
    wf.project_uuid = "proj-1"
    wf.logger = lambda *a, **k: None
    wf.get_name = lambda: "wfname"
    wf.get_access_token = lambda mid: "tok"
    wf.set_environment = lambda mid: None
    return wf


def test_list_runner_files_strips_prefix_and_keeps_size():
    wf = _make_wf()
    fake = [
        {"name": "imp1234567/stageout/mass.png", "size": 10},
        {"name": "imp1234567/stageout/ntuple.root", "size": 999},
    ]
    with mock.patch.object(reana_workflow, "REANA_AVAILABLE", True), \
         mock.patch.object(reana_workflow, "client") as cli:
        cli.list_files.return_value = fake
        out = wf.list_runner_files("1234567abc", "stageout")
    assert {"name": "mass.png", "size": 10} in out
    assert {"name": "ntuple.root", "size": 999} in out


def test_list_runner_files_normalizes_reana_size_dict():
    """REANA reports size as {"raw": int, "human_readable": str}; it must be
    flattened to int bytes so the file_status JSON contract (size: int) holds
    and the client's _human_size() does not choke on a dict."""
    wf = _make_wf()
    fake = [
        {"name": "imp1234567/stageout/mass.png",
         "size": {"raw": 2048, "human_readable": "2 KiB"}},
        {"name": "imp1234567/stageout/ntuple.root",
         "size": {"raw": 1048576, "human_readable": "1 MiB"}},
    ]
    with mock.patch.object(reana_workflow, "REANA_AVAILABLE", True), \
         mock.patch.object(reana_workflow, "client") as cli:
        cli.list_files.return_value = fake
        out = wf.list_runner_files("1234567abc", "stageout")
    assert {"name": "mass.png", "size": 2048} in out
    assert {"name": "ntuple.root", "size": 1048576} in out
    assert all(isinstance(f["size"], int) for f in out)


def test_download_selected_only_matching_and_skips_existing(tmp_path):
    wf = _make_wf()
    home = tmp_path
    storage = home / ".Yuki" / "Storage" / "proj-1" / "imp7" / "runner-1" / "stageout"
    storage.mkdir(parents=True)
    (storage / "already.png").write_bytes(b"old")     # pre-existing -> skip
    fake = [
        {"name": "impimp7000/stageout/already.png", "size": 3},
        {"name": "impimp7000/stageout/new.png", "size": 4},
        {"name": "impimp7000/stageout/ntuple.root", "size": 5},
    ]
    with mock.patch.dict(os.environ, {"HOME": str(home)}), \
         mock.patch.object(reana_workflow, "REANA_AVAILABLE", True), \
         mock.patch.object(reana_workflow, "client") as cli:
        cli.list_files.return_value = fake
        cli.download_file.return_value = (b"data",)
        wf.download_selected("imp7", reana_workflow.file_types.is_plot, "stageout")
        downloaded = {c.args[1] for c in cli.download_file.call_args_list}
    assert "impimp7000/stageout/new.png" in downloaded      # matched plot, missing
    assert "impimp7000/stageout/already.png" not in downloaded   # skipped (exists)
    assert "impimp7000/stageout/ntuple.root" not in downloaded   # not a plot
    assert not (storage / "stageout.downloaded").exists()        # no marker on partial
