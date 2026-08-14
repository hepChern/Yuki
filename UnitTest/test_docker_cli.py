"""Tests for the `yuki docker` CLI commands (Yuki/main.py)."""
from unittest import mock

from click.testing import CliRunner

from Yuki.main import cli


def _run(args, info_stdout=''):
    """Invoke the CLI with subprocess mocked; info_stdout fakes `docker info` output."""
    runner = CliRunner()
    with mock.patch("Yuki.main.subprocess.run") as m_run:
        m_run.return_value.stdout = info_stdout
        result = runner.invoke(cli, args)
    return result, m_run


def _run_cmd(m_run):
    """The final subprocess call is the `docker run` command itself."""
    return m_run.call_args_list[-1][0][0]


def test_docker_run_mounts_storage_at_yuki_home(tmp_path):
    """Storage must mount at /home/yuki/.Yuki (non-root image), not /root/.Yuki."""
    result, m_run = _run(["docker", "run", "--yuki-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    cmd = _run_cmd(m_run)
    mount = cmd[cmd.index("-v") + 1]
    assert mount == f"{tmp_path}:/home/yuki/.Yuki"
    assert "/root/.Yuki" not in cmd
    assert "--user" not in cmd


def test_docker_run_rootless_runs_as_root_with_root_storage(tmp_path):
    """Rootless Docker: container uid 1000 maps to an unwritable host subuid,
    so run as root (maps to the invoking host user) and mount at /root/.Yuki."""
    result, m_run = _run(["docker", "run", "--yuki-dir", str(tmp_path)],
                         info_stdout='["name=rootless"]')

    assert result.exit_code == 0, result.output
    cmd = _run_cmd(m_run)
    mount = cmd[cmd.index("-v") + 1]
    assert mount == f"{tmp_path}:/root/.Yuki"
    assert "--user" in cmd
    assert cmd[cmd.index("--user") + 1] == "root"


def test_docker_run_default_image_and_port():
    result, m_run = _run(["docker", "run"])

    assert result.exit_code == 0, result.output
    cmd = _run_cmd(m_run)
    assert cmd[-1] == "yuki:latest"
    assert "3315:3315" in cmd


def test_docker_run_celebi_dir_mount(tmp_path):
    celebi = tmp_path / "CelebiChrono"
    celebi.mkdir()
    result, m_run = _run(["docker", "run", "--celebi-dir", str(celebi)])

    assert result.exit_code == 0, result.output
    cmd = _run_cmd(m_run)
    assert f"{celebi}:/app/CelebiChrono" in cmd


def test_docker_run_precreates_missing_yuki_dir(tmp_path):
    """The CLI must create a missing host storage dir as the invoking user.
    Otherwise the Docker daemon creates it as root at mount time and the
    non-root container (uid 1000) cannot write to it."""
    target = tmp_path / "fresh-yuki-dir"
    result, _ = _run(["docker", "run", "--yuki-dir", str(target)])

    assert result.exit_code == 0, result.output
    assert target.is_dir()


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
