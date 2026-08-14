"""Tests for ContainerJob output discovery."""
from Yuki.kernel.container_job import ContainerJob


def _make_container(path, machine_id="runner-1"):
    c = ContainerJob.__new__(ContainerJob, str(path), machine_id)
    c.path = str(path)
    c.machine_id = machine_id
    return c


def test_outputs_returns_relative_paths_for_nested_stageout(tmp_path):
    """Outputs under a nested stageout directory come back relative."""
    c = _make_container(tmp_path)
    stageout = tmp_path / "runner-1" / "stageout"
    (stageout / "plots").mkdir(parents=True)
    (stageout / "mass.png").write_bytes(b"img")
    (stageout / "plots" / "fit.png").write_bytes(b"img2")
    names = set(c.outputs())
    assert names == {"mass.png", "plots/fit.png"}


def test_outputs_returns_relative_paths_for_nested_rawdata(tmp_path):
    """Outputs under a nested rawdata directory come back relative."""
    c = _make_container(tmp_path, machine_id=None)
    rawdata = tmp_path / "rawdata"
    (rawdata / "data").mkdir(parents=True)
    (rawdata / "data" / "x.root").write_bytes(b"data")
    names = set(c.outputs())
    assert names == {"data/x.root"}


def test_outputs_returns_empty_when_missing(tmp_path):
    """A container with no stageout or rawdata yields no outputs."""
    c = _make_container(tmp_path)
    assert c.outputs() == []
