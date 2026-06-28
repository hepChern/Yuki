import os
from unittest import mock

from Yuki.kernel import native_workflow


def _make_wf(tmp_path):
    wf = native_workflow.NativeWorkflow.__new__(native_workflow.NativeWorkflow)
    wf.local_exec_path = str(tmp_path / "exec")
    wf.project_uuid = "proj-1"
    wf.machine_id = "runner-1"
    wf.logger = lambda *a, **k: None
    src = tmp_path / "exec" / "imp7654321" / "stageout"
    src.mkdir(parents=True)
    (src / "mass.png").write_bytes(b"img")
    (src / "ntuple.root").write_bytes(b"data")
    return wf


def test_list_runner_files_native(tmp_path):
    wf = _make_wf(tmp_path)
    out = {f["name"]: f["size"] for f in wf.list_runner_files("7654321xyz", "stageout")}
    assert out["mass.png"] == 3
    assert "ntuple.root" in out


def test_download_selected_native_only_plots(tmp_path):
    wf = _make_wf(tmp_path)
    with mock.patch.dict(os.environ, {"HOME": str(tmp_path / "home")}):
        wf.download_selected("7654321xyz", native_workflow.file_types.is_plot, "stageout")
        dst = (tmp_path / "home" / ".Yuki" / "Storage" / "proj-1"
               / "7654321xyz" / "runner-1" / "stageout")
        assert (dst / "mass.png").exists()
        assert not (dst / "ntuple.root").exists()
        assert not (dst.parent / "stageout.downloaded").exists()
