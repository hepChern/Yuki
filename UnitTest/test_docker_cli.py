"""Tests for the `yuki docker` CLI commands (Yuki/main.py)."""
from unittest import mock

from click.testing import CliRunner

from Yuki.main import cli


def _run(args):
    runner = CliRunner()
    with mock.patch("Yuki.main.subprocess.run") as m_run:
        result = runner.invoke(cli, args)
    return result, m_run


def test_docker_run_mounts_storage_at_yuki_home(tmp_path):
    """Storage must mount at /home/yuki/.Yuki (non-root image), not /root/.Yuki."""
    result, m_run = _run(["docker", "run", "--yuki-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    cmd = m_run.call_args[0][0]
    mount = cmd[cmd.index("-v") + 1]
    assert mount == f"{tmp_path}:/home/yuki/.Yuki"
    assert "/root/.Yuki" not in cmd


def test_docker_run_default_image_and_port():
    result, m_run = _run(["docker", "run"])

    assert result.exit_code == 0, result.output
    cmd = m_run.call_args[0][0]
    assert cmd[-1] == "yuki:latest"
    assert "3315:3315" in cmd


def test_docker_run_celebi_dir_mount(tmp_path):
    celebi = tmp_path / "CelebiChrono"
    celebi.mkdir()
    result, m_run = _run(["docker", "run", "--celebi-dir", str(celebi)])

    assert result.exit_code == 0, result.output
    cmd = m_run.call_args[0][0]
    assert f"{celebi}:/app/CelebiChrono" in cmd


def test_docker_run_rejects_missing_dev_dir():
    result, _ = _run(["docker", "run", "--dev-dir", "/nonexistent-xyz"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_docker_restart_syncs_source_as_default_user():
    """The exec'd cp runs as the image user (yuki owns /app/Yuki in the dev image)."""
    result, m_run = _run(["docker", "restart", "mycontainer"])

    assert result.exit_code == 0, result.output
    exec_cmd = m_run.call_args_list[0][0][0]
    assert exec_cmd[:2] == ["docker", "exec"]
    assert "mycontainer" in exec_cmd
    assert "/mnt/yuki-source/." in exec_cmd
    assert "/app/Yuki/" in exec_cmd
