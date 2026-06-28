import os
from unittest import mock

from Yuki.kernel import reana_booker


def _booker():
    b = reana_booker.ReanaBooker.__new__(reana_booker.ReanaBooker)
    b.access_token = "tok"
    b._notify = lambda *a, **k: None
    return b


def _layout(tmp_path):
    base = tmp_path / ".Yuki" / "Storage" / "proj-1" / "imp-abc" / "runner-1"
    (base / "stageout").mkdir(parents=True)
    (base / "logs").mkdir(parents=True)
    (base / "stageout" / "mass.png").write_bytes(b"img")
    (base / "stageout" / "ntuple.root").write_bytes(b"data")
    (base / "logs" / "celebi.stdout").write_bytes(b"log")
    return base


def _run(booker, tmp_path, mode):
    cfg = tmp_path / "proj" / ".celebi"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text('{"project_uuid": "proj-1"}')
    meta = {"objects": [{"impression": "imp-abc"}]}
    uploaded = []
    with mock.patch.dict(os.environ, {"YUKIDIR": str(tmp_path / ".Yuki")}), \
         mock.patch.object(reana_booker, "reana_client") as rc:
        rc.upload_file.side_effect = lambda **kw: uploaded.append(kw["file_name"])
        booker._upload_stageout_files("wf", str(tmp_path / "proj"), meta, upload_mode=mode)
    return uploaded


def test_default_plots_and_logs(tmp_path):
    _layout(tmp_path)
    names = _run(_booker(), tmp_path, "plots+logs")
    assert any(n.endswith("stageout/mass.png") for n in names)
    assert any(n.endswith("logs/celebi.stdout") for n in names)
    assert not any(n.endswith("ntuple.root") for n in names)   # data excluded


def test_all_includes_data(tmp_path):
    _layout(tmp_path)
    names = _run(_booker(), tmp_path, "all")
    assert any(n.endswith("stageout/ntuple.root") for n in names)
