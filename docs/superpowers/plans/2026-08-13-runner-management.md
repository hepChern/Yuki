# Runner Management Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-runner settings (workdir/cores/mem/conda/snakemake paths, ssh params), on-demand capability testing with persisted health, and a much-improved `celebi-cli` runner UX — across Yuki (server) and Celebi (client).

**Architecture:** Yuki stores new per-runner data in a nested `runner_settings` map (plus a `runner_health` map) inside `~/.Yuki/config.json`; old flat maps are untouched and ssh fields fall back to them. A new synchronous `GET /test-runner/<name>` endpoint probes snakemake/conda/workdir (locally for native, over SSH for ssh, ping for reana) and persists results. Celebi's `ChernCommunicator` gains a `test_runner` method and extra register fields; `celebi-cli` gains `test-runner` plus new options on `register-runner`/`update-runner`.

**Tech Stack:** Python 3.8+, Flask, Click, paramiko (optional import), `CelebiChrono.utils.metadata.ConfigFile`, unittest/pytest with heavy mocking.

**Spec:** `docs/superpowers/specs/2026-08-13-runner-management-design.md` (in the Yuki repo)

## Global Constraints

- **Compat-first:** existing endpoints keep behavior and response shapes; new data is additive only. Old Celebi clients must keep working against new Yuki.
- Two repos: **Yuki** at `/Users/wave/workdir/Celebi/Yuki`, **Celebi** at `/Users/wave/workdir/Celebi/Celebi` (package `CelebiChrono`). Tasks are labeled per repo; commits go to the repo being changed.
- Absolute imports from package root (`from Yuki.kernel...`, `from CelebiChrono...`).
- Python 3.8-compatible syntax (CI lints 3.8–3.10): no `X | Y` unions, no match statements.
- Tests never touch real `~/.Yuki` or `~/.celebi` — always temp dirs / mocks.
- Yuki tests are pytest-style functions with `monkeypatch` (see `UnitTest/test_runner_routes.py`); Celebi tests are unittest classes using `prepare.create_chern_project` (see `UnitTest/test_cherncommunicator.py`).
- Run Yuki tests from `/Users/wave/workdir/Celebi/Yuki` with `python -m pytest UnitTest/<file> -v`; Celebi tests from `/Users/wave/workdir/Celebi/Celebi` with `python -m unittest UnitTest.<module> -v`.
- Commit message style: conventional commits (`feat:`, `fix:`, `test:`), ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## Part 1 — Yuki (server)

### Task 1: Runner settings/health config helpers

**Files:**
- Create: `Yuki/kernel/runner_config.py`
- Test: `UnitTest/test_runner_config.py`

**Interfaces:**
- Produces (used by Tasks 2–6):
  - `open_config() -> ConfigFile` — `ConfigFile` for `$YUKIDIR/config.json` (YUKIDIR env, default `~/.Yuki`).
  - `get_runner_settings(config_file, runner_id) -> dict` — the `runner_settings[runner_id]` entry, `{}` if absent.
  - `merge_runner_settings(config_file, runner_id, updates: dict) -> None` — merge `updates` into `runner_settings[runner_id]` and write back.
  - `get_ssh_settings(config_file, runner_id) -> dict` — keys `host`, `user`, `key_path`, `port`, `remote_workdir`, `cores`, `conda_path`, `snakemake_path`; ssh connection fields read `runner_settings` first, falling back to the old `ssh_hosts`/`ssh_users`/`ssh_key_paths`/`ssh_ports`/`remote_workdirs` maps.
  - `get_runner_health(config_file, runner_id) -> dict` — `runner_health[runner_id]`, or `{"status": "untested"}`.
  - `set_runner_health(config_file, runner_id, health: dict) -> None`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for runner settings/health config helpers."""
import os
import tempfile

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.kernel import runner_config


def _cfg():
    tmp = tempfile.mkdtemp()
    return ConfigFile(os.path.join(tmp, "config.json"))


def test_runner_settings_roundtrip():
    cfg = _cfg()
    assert runner_config.get_runner_settings(cfg, "r1") == {}
    runner_config.merge_runner_settings(cfg, "r1", {"workdir": "/data", "cores": 8})
    runner_config.merge_runner_settings(cfg, "r1", {"mem_mb": 4096})
    assert runner_config.get_runner_settings(cfg, "r1") == {
        "workdir": "/data", "cores": 8, "mem_mb": 4096,
    }


def test_ssh_settings_prefer_new_map():
    cfg = _cfg()
    cfg.write_variable("ssh_hosts", {"r1": "old.example.com"})
    cfg.write_variable("ssh_users", {"r1": "olduser"})
    cfg.write_variable("runner_settings", {"r1": {"ssh_host": "new.example.com",
                                                  "cores": 4}})
    s = runner_config.get_ssh_settings(cfg, "r1")
    assert s["host"] == "new.example.com"   # new map wins
    assert s["user"] == "olduser"           # falls back to old map
    assert s["port"] == 22                  # default
    assert s["remote_workdir"] == "/tmp/yuki-workflows"  # default
    assert s["cores"] == 4


def test_ssh_settings_old_runner_no_migration():
    cfg = _cfg()
    cfg.write_variable("ssh_hosts", {"r1": "h"})
    cfg.write_variable("ssh_users", {"r1": "u"})
    cfg.write_variable("ssh_key_paths", {"r1": "/k"})
    cfg.write_variable("ssh_ports", {"r1": 2222})
    cfg.write_variable("remote_workdirs", {"r1": "/remote"})
    s = runner_config.get_ssh_settings(cfg, "r1")
    assert s == {"host": "h", "user": "u", "key_path": "/k", "port": 2222,
                 "remote_workdir": "/remote", "cores": "all",
                 "conda_path": "", "snakemake_path": ""}


def test_runner_health_roundtrip():
    cfg = _cfg()
    assert runner_config.get_runner_health(cfg, "r1") == {"status": "untested"}
    runner_config.set_runner_health(cfg, "r1", {"status": "ok", "checks": {}})
    assert runner_config.get_runner_health(cfg, "r1")["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_runner_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Yuki.kernel.runner_config'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Helpers for per-runner settings and health stored in ~/.Yuki/config.json.

New-style data lives in the nested ``runner_settings`` / ``runner_health``
maps keyed by runner id. Legacy ssh fields live in the flat ``ssh_*`` maps;
``get_ssh_settings`` reads new-map-first and falls back to the legacy maps so
old runners keep working without migration.
"""
import os

from CelebiChrono.utils.metadata import ConfigFile

LEGACY_SSH_KEYS = ("ssh_hosts", "ssh_users", "ssh_key_paths",
                   "ssh_ports", "remote_workdirs")


def open_config():
    """Open the Yuki config file ($YUKIDIR/config.json)."""
    yuki_dir = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
    return ConfigFile(os.path.join(yuki_dir, "config.json"))


def get_runner_settings(config_file, runner_id):
    """Return the runner_settings entry for runner_id ({} if absent)."""
    settings = config_file.read_variable("runner_settings", {})
    return dict(settings.get(runner_id, {}))


def merge_runner_settings(config_file, runner_id, updates):
    """Merge updates into runner_settings[runner_id] and persist."""
    settings = config_file.read_variable("runner_settings", {})
    entry = dict(settings.get(runner_id, {}))
    entry.update(updates)
    settings[runner_id] = entry
    config_file.write_variable("runner_settings", settings)


def get_ssh_settings(config_file, runner_id):
    """Return merged ssh settings, new map preferred over legacy maps."""
    s = get_runner_settings(config_file, runner_id)
    legacy = {key: config_file.read_variable(key, {}) for key in LEGACY_SSH_KEYS}

    def pick(new_key, legacy_key, default):
        if s.get(new_key):
            return s[new_key]
        return legacy[legacy_key].get(runner_id, default)

    return {
        "host": pick("ssh_host", "ssh_hosts", ""),
        "user": pick("ssh_user", "ssh_users", ""),
        "key_path": pick("ssh_key_path", "ssh_key_paths", ""),
        "port": pick("ssh_port", "ssh_ports", 22),
        "remote_workdir": pick("remote_workdir", "remote_workdirs",
                               "/tmp/yuki-workflows"),
        "cores": s.get("cores", "all"),
        "conda_path": s.get("conda_path", ""),
        "snakemake_path": s.get("snakemake_path", ""),
    }


def get_runner_health(config_file, runner_id):
    """Return the persisted health entry, or {'status': 'untested'}."""
    health = config_file.read_variable("runner_health", {})
    return dict(health.get(runner_id, {"status": "untested"}))


def set_runner_health(config_file, runner_id, health):
    """Persist a health entry for runner_id."""
    all_health = config_file.read_variable("runner_health", {})
    all_health[runner_id] = health
    config_file.write_variable("runner_health", all_health)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_runner_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/runner_config.py UnitTest/test_runner_config.py
git commit -m "feat(kernel): runner settings/health config helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `/test-runner` and `/runner-health` endpoints

**Files:**
- Create: `Yuki/server/runner_probe.py`
- Modify: `Yuki/server/routes/runner.py` (append endpoints at end of file)
- Test: `UnitTest/test_runner_probe.py`

**Interfaces:**
- Consumes: `runner_config` helpers from Task 1; existing `_ssh_ping` and `ping` in `Yuki/server/routes/runner.py`; existing `config` object pattern from `..config`.
- Produces:
  - `runner_probe.probe_native(settings: dict) -> dict` — returns `{"snakemake": {...}, "conda": {...}, "workdir_writable": {...}}`.
  - `runner_probe.probe_ssh(ssh_settings: dict) -> dict` — same check keys plus `connectivity`.
  - `runner_probe.probe_reana(url: str, token: str) -> dict` — `{"connectivity": {...}}`.
  - `GET /test-runner/<runner>` → JSON `{"status": "ok"|"failed", "checked_at": ISO, "checks": {...}}`; unknown runner → 404 `{"error": ...}`.
  - `GET /runner-health/<runner>` → persisted health JSON; unknown runner → 404.

Probe details:
- `probe_native`: locate snakemake via `settings.get("snakemake_path")` or `shutil.which("snakemake")`; if found run `[path, "--version"]` via `subprocess.run(..., capture_output=True, text=True, timeout=10)` and record `{"ok": True, "version": stdout.strip(), "path": path}`; missing → `{"ok": False, "error": "not found in PATH"}`. Same for conda via `conda_path`/`which("conda")`. Workdir: `settings.get("workdir")` or `~/.Yuki/LocalWorkflows`; `os.makedirs(workdir, exist_ok=True)` then `os.access(workdir, os.W_OK)`.
- `probe_ssh`: paramiko connect (same kwargs as `_ssh_ping`: timeout 10); then `exec_command` (10s timeout) for `command -v snakemake && snakemake --version`, same for conda, and `test -w <remote_workdir>`. Each check wraps its own exceptions into `{"ok": False, "error": str(e)}`.
- `probe_reana`: reuse `Yuki.server.utils.ping(url, token)`; ok iff it indicates success (inspect its return: treat any non-exception return containing no error as ok — follow existing `runner-connection` behavior).
- Backend `"dry"` or unknown non-reana/non-ssh: connectivity-only, always ok.
- Overall `status`: `"failed"` if any check has `ok: False`, else `"ok"`. `checked_at` via `datetime.datetime.now().isoformat(timespec="seconds")`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for runner capability probing and test-runner endpoints."""
import json
import os
import subprocess
import tempfile
from unittest import mock

from CelebiChrono.utils.metadata import ConfigFile
from Yuki.server import runner_probe
from Yuki.server.routes import runner as runner_routes


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _temp_config(monkeypatch):
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, ".Yuki"), exist_ok=True)
    config_obj = mock.MagicMock()
    config_obj.config_path = os.path.join(tmp, ".Yuki", "config.json")
    config_obj.get_config_file.return_value = ConfigFile(config_obj.config_path)
    monkeypatch.setattr(runner_routes, "config", config_obj)
    return config_obj


def _write_runner(config_obj, name="local", backend="native", settings=None):
    runner_id = "r-uuid"
    data = {
        "runners": [name],
        "runners_id": {name: runner_id},
        "urls": {runner_id: ""},
        "tokens": {runner_id: ""},
        "backend_types": {runner_id: backend},
    }
    if settings:
        data["runner_settings"] = {runner_id: settings}
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return runner_id


def test_probe_native_all_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_probe.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_probe.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "8.1.0\n", ""))
    checks = runner_probe.probe_native({"workdir": str(tmp_path)})
    assert checks["snakemake"]["ok"] is True
    assert checks["snakemake"]["version"] == "8.1.0"
    assert checks["conda"]["ok"] is True
    assert checks["workdir_writable"]["ok"] is True


def test_probe_native_missing_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: None)
    checks = runner_probe.probe_native({"workdir": str(tmp_path)})
    assert checks["snakemake"]["ok"] is False
    assert "not found" in checks["snakemake"]["error"]
    assert checks["conda"]["ok"] is False
    assert checks["workdir_writable"]["ok"] is True


def test_test_runner_native_persists_health(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch)
    runner_id = _write_runner(config_obj, settings={"workdir": str(tmp_path)})
    monkeypatch.setattr(runner_probe.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_probe.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "1.0\n", ""))

    r = _app(runner_routes.bp).test_client().get("/test-runner/local")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert "checked_at" in body

    cfg = json.load(open(config_obj.config_path, encoding="utf-8"))
    assert cfg["runner_health"][runner_id]["status"] == "ok"


def test_test_runner_ssh_failure_marks_failed(monkeypatch):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj, name="cluster", backend="ssh",
                  settings={"ssh_host": "h", "ssh_user": "u"})
    with mock.patch("paramiko.SSHClient") as ssh_cls:
        ssh_cls.return_value.connect.side_effect = Exception("no route")
        r = _app(runner_routes.bp).test_client().get("/test-runner/cluster")
    body = r.get_json()
    assert body["status"] == "failed"
    assert body["checks"]["connectivity"]["ok"] is False


def test_test_runner_unknown_404(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().get("/test-runner/ghost")
    assert r.status_code == 404


def test_runner_health_untested_and_persisted(monkeypatch, tmp_path):
    config_obj = _temp_config(monkeypatch)
    _write_runner(config_obj)
    client = _app(runner_routes.bp).test_client()
    assert client.get("/runner-health/local").get_json() == {"status": "untested"}

    monkeypatch.setattr(runner_probe.shutil, "which", lambda name: None)
    client.get("/test-runner/local")
    assert client.get("/runner-health/local").get_json()["status"] == "failed"
```

Note: `test_runner_health_untested_and_persisted` uses the default workdir `~/.Yuki/LocalWorkflows` — the implementation must build that path from `$YUKIDIR` (set it in the test via `monkeypatch.setenv("YUKIDIR", str(tmp_path))`) so the writability check stays inside the temp dir.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_runner_probe.py -v`
Expected: FAIL — `No module named 'Yuki.server.runner_probe'`; 404s for the new routes.

- [ ] **Step 3: Write minimal implementation**

`Yuki/server/runner_probe.py`:

```python
"""Capability probing for runners (snakemake / conda / workdir)."""
import datetime
import os
import shutil
import subprocess

PROBE_TIMEOUT = 10


def _ok(**extra):
    return {"ok": True, **extra}


def _err(error):
    return {"ok": False, "error": str(error)}


def _probe_tool(path_setting, binary):
    """Probe one executable: configured path, else PATH lookup."""
    path = path_setting or shutil.which(binary)
    if not path:
        return _err(f"{binary} not found in PATH")
    try:
        result = subprocess.run([path, "--version"], capture_output=True,
                                text=True, timeout=PROBE_TIMEOUT, check=False)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _err(f"{binary} at {path} failed: {exc}")
    if result.returncode != 0:
        return _err(f"{binary} --version exited {result.returncode}: "
                    f"{result.stderr.strip()}")
    return _ok(version=result.stdout.strip(), path=path)


def _default_workdir():
    yuki_dir = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
    return os.path.join(yuki_dir, "LocalWorkflows")


def probe_native(settings):
    """Probe snakemake/conda/workdir on the Yuki host."""
    checks = {
        "snakemake": _probe_tool(settings.get("snakemake_path", ""), "snakemake"),
        "conda": _probe_tool(settings.get("conda_path", ""), "conda"),
    }
    workdir = settings.get("workdir") or _default_workdir()
    try:
        os.makedirs(workdir, exist_ok=True)
        checks["workdir_writable"] = (
            _ok(path=workdir) if os.access(workdir, os.W_OK)
            else _err(f"{workdir} is not writable"))
    except OSError as exc:
        checks["workdir_writable"] = _err(str(exc))
    return checks


def probe_ssh(ssh_settings):
    """Probe connectivity plus snakemake/conda/workdir on the remote host."""
    try:
        import paramiko
    except ImportError:
        return {"connectivity": _err("paramiko is not installed")}

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        key_path = ssh_settings.get("key_path") or None
        if key_path:
            key_path = os.path.expanduser(key_path)
            if not os.path.exists(key_path):
                key_path = None
        client.connect(hostname=ssh_settings.get("host", ""),
                       port=ssh_settings.get("port", 22),
                       username=ssh_settings.get("user", ""),
                       key_filename=key_path,
                       timeout=PROBE_TIMEOUT, banner_timeout=PROBE_TIMEOUT)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"connectivity": _err(exc)}
    finally:
        if client.get_transport() is None:
            client.close()

    checks = {"connectivity": _ok()}

    def remote(cmd):
        _, stdout, stderr = client.exec_command(cmd, timeout=PROBE_TIMEOUT)
        return stdout.read().decode().strip(), stderr.read().decode().strip()

    try:
        for name, setting, binary in (
                ("snakemake", "snakemake_path", "snakemake"),
                ("conda", "conda_path", "conda")):
            tool = ssh_settings.get(setting) or binary
            out, err = remote(f"{tool} --version")
            checks[name] = _err(err or f"{binary} not usable") if err else _ok(version=out)
        workdir = ssh_settings.get("remote_workdir", "/tmp/yuki-workflows")
        _, err = remote(f"mkdir -p {workdir} && test -w {workdir}")
        checks["workdir_writable"] = _err(err) if err else _ok(path=workdir)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        checks.setdefault("snakemake", _err(exc))
    finally:
        client.close()
    return checks


def probe_reana(url, token, ping_func):
    """Probe a REANA runner via the existing ping helper."""
    try:
        result = ping_func(url, token)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"connectivity": _err(exc)}
    if isinstance(result, dict) and result.get("status") not in (None, "Connected"):
        return {"connectivity": _err(result.get("message", "ping failed"))}
    return {"connectivity": _ok()}


def summarize(checks):
    """Build the persisted health record from check results."""
    return {
        "status": "failed" if any(not c.get("ok") for c in checks.values()) else "ok",
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "checks": checks,
    }
```

New endpoints appended to `Yuki/server/routes/runner.py` (add `from ...kernel import runner_config` and `from .. import runner_probe` imports at top):

```python
@bp.route("/test-runner/<runner>", methods=['GET'])
def test_runner(runner):
    """Probe a runner's capabilities and persist the result."""
    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    if runner not in runners_id:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404
    runner_id = runners_id[runner]
    backend_types = config_file.read_variable("backend_types", {})
    backend_type = backend_types.get(runner_id, "reana")
    settings = runner_config.get_runner_settings(config_file, runner_id)

    if backend_type == "ssh":
        checks = runner_probe.probe_ssh(
            runner_config.get_ssh_settings(config_file, runner_id))
    elif backend_type == "reana":
        urls = config_file.read_variable("urls", {})
        tokens = config_file.read_variable("tokens", {})
        checks = runner_probe.probe_reana(
            urls.get(runner_id, ""), tokens.get(runner_id, ""), ping)
    elif backend_type == "native":
        checks = runner_probe.probe_native(settings)
    else:  # dry / unknown backends: connectivity-only, always ok
        checks = {"connectivity": {"ok": True}}

    health = runner_probe.summarize(checks)
    runner_config.set_runner_health(config_file, runner_id, health)
    return jsonify(health)


@bp.route("/runner-health/<runner>", methods=['GET'])
def runner_health(runner):
    """Return the persisted health of a runner (never re-probes)."""
    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    if runner not in runners_id:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404
    return jsonify(runner_config.get_runner_health(config_file,
                                                   runners_id[runner]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_runner_probe.py UnitTest/test_runner_config.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/runner_probe.py Yuki/server/routes/runner.py UnitTest/test_runner_probe.py
git commit -m "feat(server): /test-runner and /runner-health capability probing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: register/update settings + validation fixes

**Files:**
- Modify: `Yuki/server/routes/runner.py` (`registerrunner` L94–127, `update_runner` L222–271, `runnersurl` L55–62, `machine_id` L318–323)
- Test: `UnitTest/test_runner_routes.py` (append tests)

**Interfaces:**
- Consumes: `runner_config.merge_runner_settings` (Task 1).
- Produces: `/register-runner` accepts optional form fields `workdir`, `cores`, `mem_mb`, `conda_path`, `snakemake_path`; `/update-runner/<runner>` accepts the same keys in its JSON body. Celebi Tasks 7–9 send exactly these field names.

Changes:
1. `registerrunner`: validate `runner`/`url`/`token` present (url/token may be empty strings for ssh/native — only the *keys* must be present; missing key → 400 with plain-text message). Duplicate name → 409 plain text `"runner '<name>' already exists"`. New optional form fields collected into a dict and stored via `merge_runner_settings` (only keys actually present in the form). ssh: keep existing `_write_ssh_config` call (legacy maps) **and** also write the ssh fields into `runner_settings` via `merge_runner_settings` (double-write). `cores`/`mem_mb` coerced with `int(...)` in a try/except (skip on failure).
2. `update_runner`: same five new keys from the JSON body → `merge_runner_settings`.
3. `runnersurl`: build with `runners_url.get(runners_id.get(r, ""), "")` and filter out empties.
4. `machine_id`: unknown machine → 404 plain text `"machine not found"`.

- [ ] **Step 1: Write the failing test** (append to `UnitTest/test_runner_routes.py`)

```python
def test_register_runner_stores_native_settings(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    r = c.post("/register-runner", data={
        "runner": "local", "url": "", "token": "", "backend_type": "native",
        "workdir": "/data/yuki", "cores": "8", "mem_mb": "4096",
        "conda_path": "/opt/conda/bin/conda",
    })
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["local"]
    assert cfg["runner_settings"][runner_id] == {
        "workdir": "/data/yuki", "cores": 8, "mem_mb": 4096,
        "conda_path": "/opt/conda/bin/conda",
    }


def test_register_runner_duplicate_name_409(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    c.post("/register-runner", data={"runner": "local", "url": "",
                                     "token": "", "backend_type": "native"})
    r = c.post("/register-runner", data={"runner": "local", "url": "",
                                         "token": "", "backend_type": "native"})
    assert r.status_code == 409
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert cfg["runners"].count("local") == 1


def test_register_runner_missing_field_400(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().post(
        "/register-runner", data={"runner": "local"})
    assert r.status_code == 400


def test_register_runner_ssh_double_writes_settings(monkeypatch):
    _temp_config(monkeypatch)
    c = _app(runner_routes.bp).test_client()
    c.post("/register-runner", data={
        "runner": "cluster", "url": "", "token": "", "backend_type": "ssh",
        "ssh_host": "h", "ssh_user": "u", "ssh_key_path": "/k",
        "ssh_port": "2222", "remote_workdir": "/remote",
    })
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    runner_id = cfg["runners_id"]["cluster"]
    # legacy maps still written
    assert cfg["ssh_hosts"][runner_id] == "h"
    # new map also written
    s = cfg["runner_settings"][runner_id]
    assert s["ssh_host"] == "h" and s["ssh_port"] == 2222
    assert s["remote_workdir"] == "/remote"


def test_update_runner_stores_settings(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["local"], "runners_id": {"local": runner_id},
                   "backend_types": {runner_id: "native"}}, f)
    r = _app(runner_routes.bp).test_client().patch(
        "/update-runner/local", json={"cores": 16, "snakemake_path": "/usr/bin/snakemake"})
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert cfg["runner_settings"][runner_id]["cores"] == 16
    assert cfg["runner_settings"][runner_id]["snakemake_path"] == "/usr/bin/snakemake"


def test_machine_id_unknown_404(monkeypatch):
    _temp_config(monkeypatch)
    r = _app(runner_routes.bp).test_client().get("/machine-id/ghost")
    assert r.status_code == 404


def test_runners_url_tolerates_missing_entries(monkeypatch):
    _temp_config(monkeypatch)
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["a", "b"], "runners_id": {"a": "id-a"},
                   "urls": {}}, f)  # "b" has no id, "a" has no url
    r = _app(runner_routes.bp).test_client().get("/runners-url")
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_runner_routes.py -v -k "settings or duplicate or missing_field or double_writes or machine_id or tolerates"`
Expected: FAIL (no `runner_settings` written; duplicates appended; 500 on unknown machine)

- [ ] **Step 3: Write minimal implementation**

In `registerrunner` (replace the body):

```python
@bp.route("/register-runner", methods=['POST'])
def registerrunner():
    """Register a new runner."""
    for field in ("runner", "url", "token"):
        if field not in request.form:
            return f"missing required field: {field}", 400
    runner = request.form["runner"]
    backend_type = request.form.get("backend_type", "native")

    config_file = config.get_config_file()
    runners_list = config_file.read_variable("runners", [])
    if runner in runners_list:
        return f"runner '{runner}' already exists", 409

    runner_id = csys.generate_uuid()
    runners_id = config_file.read_variable("runners_id", {})
    runners_url = config_file.read_variable("urls", {})
    tokens = config_file.read_variable("tokens", {})
    backend_types = config_file.read_variable("backend_types", {})

    runners_list.append(runner)
    runners_id[runner] = runner_id
    runners_url[runner_id] = request.form["url"]
    tokens[runner_id] = request.form["token"]
    backend_types[runner_id] = backend_type

    config_file.write_variable("runners", runners_list)
    config_file.write_variable("runners_id", runners_id)
    config_file.write_variable("urls", runners_url)
    config_file.write_variable("tokens", tokens)
    config_file.write_variable("backend_types", backend_types)

    if backend_type == "ssh":
        _write_ssh_config(config_file, runner_id, request.form)

    settings = _collect_settings(request.form)
    if settings:
        runner_config.merge_runner_settings(config_file, runner_id, settings)
    return "successful"
```

New module-level helper in `runner.py`:

```python
_SETTING_FIELDS = ("workdir", "conda_path", "snakemake_path",
                   "ssh_host", "ssh_user", "ssh_key_path", "remote_workdir")
_SETTING_INT_FIELDS = ("cores", "mem_mb", "ssh_port")


def _collect_settings(data):
    """Collect runner_settings fields from form data or a JSON dict."""
    settings = {}
    for field in _SETTING_FIELDS:
        if data.get(field):
            settings[field] = data.get(field)
    for field in _SETTING_INT_FIELDS:
        if data.get(field) is not None:
            try:
                settings[field] = int(data.get(field))
            except (ValueError, TypeError):
                pass
    return settings
```

In `update_runner`, after the existing `if "cvmfs" in data:` block (before the backend-switch handling), add:

```python
    settings = _collect_settings(data)
    if settings:
        runner_config.merge_runner_settings(config_file, runner_id, settings)
```

Replace `runnersurl` body:

```python
    urls = []
    for runner in runners_list:
        url = runners_url.get(runners_id.get(runner, ""), "")
        if url:
            urls.append(url)
    return " ".join(urls)
```

Replace `machine_id` body:

```python
    runner_ids = config_file.read_variable("runners_id", {})
    if machine not in runner_ids:
        return "machine not found", 404
    return runner_ids[machine]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_runner_routes.py UnitTest/test_runner_probe.py -v`
Expected: all pass (old tests too — note the old register test posts with empty url/token *keys present*, which passes validation)

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/runner.py UnitTest/test_runner_routes.py
git commit -m "feat(server): runner settings on register/update, validation fixes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `runners-config` settings/health keys + widened remove cleanup

**Files:**
- Modify: `Yuki/server/routes/runner.py` (`runners_config` L274–315, `removerunner` L166–206)
- Test: `UnitTest/test_runner_routes.py` (append)

**Interfaces:**
- Consumes: `runner_config.get_runner_settings` / `get_runner_health` (Task 1).
- Produces: each `/runners-config` entry gains `settings: dict` and `health: dict` keys (Celebi Task 8 renders them).

- [ ] **Step 1: Write the failing test**

```python
def test_runners_config_includes_settings_and_health(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["local"], "runners_id": {"local": runner_id},
            "urls": {runner_id: ""}, "tokens": {runner_id: ""},
            "backend_types": {runner_id: "native"},
            "runner_settings": {runner_id: {"cores": 8}},
            "runner_health": {runner_id: {"status": "ok", "checks": {}}},
        }, f)
    data = _app(runner_routes.bp).test_client().get("/runners-config").get_json()
    assert data[0]["settings"] == {"cores": 8}
    assert data[0]["health"]["status"] == "ok"


def test_runners_config_defaults_without_new_maps(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners": ["local"], "runners_id": {"local": runner_id},
                   "urls": {runner_id: ""}, "tokens": {runner_id: ""},
                   "backend_types": {runner_id: "native"}}, f)
    data = _app(runner_routes.bp).test_client().get("/runners-config").get_json()
    assert data[0]["settings"] == {}
    assert data[0]["health"] == {"status": "untested"}


def test_remove_runner_cleans_settings_health_and_stale_ssh(monkeypatch):
    _temp_config(monkeypatch)
    runner_id = "r-uuid"
    # backend flipped reana after ssh: stale ssh_* entries must still go
    with open(runner_routes.config.config_path, "w", encoding="utf-8") as f:
        json.dump({
            "runners": ["cluster"], "runners_id": {"cluster": runner_id},
            "urls": {runner_id: ""}, "tokens": {runner_id: ""},
            "backend_types": {runner_id: "reana"},
            "ssh_hosts": {runner_id: "h"},
            "runner_settings": {runner_id: {"cores": 8}},
            "runner_health": {runner_id: {"status": "ok"}},
        }, f)
    r = _app(runner_routes.bp).test_client().get("/remove-runner/cluster")
    assert r.status_code == 200
    cfg = json.load(open(runner_routes.config.config_path, encoding="utf-8"))
    assert runner_id not in cfg.get("ssh_hosts", {})
    assert runner_id not in cfg.get("runner_settings", {})
    assert runner_id not in cfg.get("runner_health", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_runner_routes.py -v -k "settings_and_health or defaults_without or stale_ssh"`
Expected: FAIL (no `settings`/`health` keys; stale ssh entries survive)

- [ ] **Step 3: Write minimal implementation**

In `runners_config`, inside the loop after building `runner_cfg` (before the `if backend_type == "ssh":` block), add:

```python
        runner_cfg["settings"] = runner_config.get_runner_settings(
            config_file, runner_id)
        runner_cfg["health"] = runner_config.get_runner_health(
            config_file, runner_id)
```

In `removerunner`, replace the conditional ssh cleanup:

```python
    if backend_type == "ssh":
        _remove_ssh_config(config_file, runner_id)
```

with unconditional cleanup:

```python
    _remove_ssh_config(config_file, runner_id)
    for key in ("runner_settings", "runner_health"):
        data = config_file.read_variable(key, {})
        if runner_id in data:
            del data[runner_id]
            config_file.write_variable(key, data)
```

(The now-unused `backend_type` local in `removerunner` can be removed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_runner_routes.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/runner.py UnitTest/test_runner_routes.py
git commit -m "feat(server): expose settings/health in runners-config; thorough remove cleanup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Native workflow consumes settings

**Files:**
- Modify: `Yuki/kernel/native_workflow.py` (`__init__` L25–37)
- Modify: `Yuki/kernel/snakemake_monitor.py` (`execute_snakemake` L48–72)
- Modify: `Yuki/main.py` (`run_workflow` L154–231)
- Test: `UnitTest/test_native_runner_settings.py` (create)

**Interfaces:**
- Consumes: `runner_config.open_config` / `get_runner_settings` (Task 1).
- Produces:
  - `SnakemakeMonitor.execute_snakemake(cores, logger=None, mem_mb=None, snakemake_path=None, conda_path=None)` — `mem_mb` adds `--resources mem_mb=<n>`; `snakemake_path` replaces the `snakemake` binary; `conda_path` prepends its dirname to `PATH` in the subprocess env.
  - `NativeWorkflow` honors `runner_settings.workdir` for `local_exec_path`.
  - `yuki run-workflow` resolves settings from the workflow's `machine_id`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for native runner settings consumption."""
import json
import os
import tempfile
from unittest import mock

from Yuki.kernel.snakemake_monitor import SnakemakeMonitor


def _monitor(tmp):
    os.makedirs(os.path.join(tmp, "wf"), exist_ok=True)
    exec_dir = os.path.join(tmp, "exec")
    os.makedirs(exec_dir, exist_ok=True)
    return SnakemakeMonitor(os.path.join(tmp, "wf"), exec_dir,
                            project_uuid="p", workflow_uuid="w")


def test_execute_snakemake_settings(tmp_path):
    mon = _monitor(str(tmp_path))
    with mock.patch("subprocess.Popen") as popen:
        popen.return_value.wait.return_value = 0
        popen.return_value.returncode = 0
        try:
            mon.execute_snakemake(8, mem_mb=4096,
                                  snakemake_path="/opt/bin/snakemake",
                                  conda_path="/opt/conda/bin/conda")
        except Exception:
            pass  # status file handling is out of scope here
    cmd = popen.call_args[0][0]
    assert cmd[0] == "/opt/bin/snakemake"
    assert "--resources" in cmd and "mem_mb=4096" in cmd
    assert "-j" in cmd and "8" in cmd
    env = popen.call_args[1].get("env")
    assert env["PATH"].startswith("/opt/conda/bin" + os.pathsep)


def test_native_workflow_uses_workdir_setting(monkeypatch, tmp_path):
    yuki_dir = tmp_path / ".Yuki"
    (yuki_dir / "Storage" / "proj").mkdir(parents=True)
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    with open(yuki_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"runner_settings": {"m1": {"workdir": str(tmp_path / "custom")}}}, f)

    from Yuki.kernel.native_workflow import NativeWorkflow
    with mock.patch.object(NativeWorkflow, "__init__", lambda self, *a, **k: None):
        wf = NativeWorkflow.__new__(NativeWorkflow)
    # exercise the path-resolution logic directly
    from Yuki.kernel import runner_config
    settings = runner_config.get_runner_settings(runner_config.open_config(), "m1")
    assert settings["workdir"] == str(tmp_path / "custom")
```

(The second test pins the helper contract the `__init__` change relies on; the `__init__` change itself is covered by the path construction below plus manual e2e.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_native_runner_settings.py -v`
Expected: FAIL — `execute_snakemake() got an unexpected keyword argument 'mem_mb'`

- [ ] **Step 3: Write minimal implementation**

`snakemake_monitor.py` — new signature and command build:

```python
    def execute_snakemake(self, cores, logger=None, mem_mb=None,
                          snakemake_path=None, conda_path=None):
        """... (extend docstring with the three new params) ..."""
        # ... unchanged until command build ...
        cmd = [
            snakemake_path or "snakemake",
            "--use-conda",
            "--conda-frontend", "conda",
            "--keep-going",
            "-j", str(cores)
        ]
        if mem_mb:
            cmd += ["--resources", f"mem_mb={int(mem_mb)}"]
        env = None
        if conda_path:
            env = dict(os.environ)
            env["PATH"] = (os.path.dirname(conda_path) + os.pathsep
                           + env.get("PATH", ""))
```

and pass `env=env` in the `subprocess.Popen(...)` call.

`native_workflow.py` `__init__` — replace the hardcoded base:

```python
        from Yuki.kernel import runner_config  # top of file instead
        settings = runner_config.get_runner_settings(
            runner_config.open_config(), self.machine_id or "")
        base_dir = settings.get("workdir") or os.path.join(
            os.environ["HOME"], ".Yuki", "LocalWorkflows")
        self.local_exec_path = os.path.join(base_dir, self.uuid)
        os.makedirs(self.local_exec_path, exist_ok=True)
```

`main.py` `run_workflow` — change `--cores` default to `None`, and after `workflow_path` is found, resolve settings:

```python
    import json as _json  # already imported as json inside function
    workflow_cfg = ConfigFile(os.path.join(workflow_path, "config.json"))
    machine_id = workflow_cfg.read_variable("machine_id", "")
    settings = runner_config.get_runner_settings(
        runner_config.open_config(), machine_id)
    cores = cores or settings.get("cores", "all")
```

(imports needed at top of function: `from CelebiChrono.utils.metadata import ConfigFile`, `from Yuki.kernel import runner_config`) and pass through:

```python
    exit_code = monitor.execute_snakemake(
        cores, logger,
        mem_mb=settings.get("mem_mb"),
        snakemake_path=settings.get("snakemake_path") or None,
        conda_path=settings.get("conda_path") or None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_native_runner_settings.py -v && python -m pytest UnitTest/ -v -k "runner or native or snakemake"`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/native_workflow.py Yuki/kernel/snakemake_monitor.py Yuki/main.py UnitTest/test_native_runner_settings.py
git commit -m "feat(kernel): native runner workdir/cores/mem/conda/snakemake settings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: SSH workflow consumes merged settings

**Files:**
- Modify: `Yuki/kernel/ssh_workflow.py` (`_load_ssh_config` L187–204, `_start_remote_snakemake` L348–372)
- Test: `UnitTest/test_ssh_runner_settings.py` (create)

**Interfaces:**
- Consumes: `runner_config.get_ssh_settings` (Task 1) — returns `host/user/key_path/port/remote_workdir/cores/conda_path/snakemake_path`.
- Produces: `self.ssh_config` gains `cores`, `conda_path`, `snakemake_path` keys; `yuki_run.sh` wrapper honors them.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for ssh runner settings consumption."""
import json
import os
from unittest import mock

from Yuki.kernel.ssh_workflow import SshWorkflow


def _workflow(tmp_path, monkeypatch, config_data):
    yuki_dir = tmp_path / ".Yuki"
    yuki_dir.mkdir(parents=True)
    (yuki_dir / "config.json").write_text(json.dumps(config_data))
    monkeypatch.setenv("YUKIDIR", str(yuki_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch.object(SshWorkflow, "__init__", lambda self, *a, **k: None):
        wf = SshWorkflow.__new__(SshWorkflow)
    wf.machine_id = "m1"
    return wf


def test_load_ssh_config_merges_new_and_legacy(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, monkeypatch, {
        "ssh_hosts": {"m1": "legacy-host"},
        "runner_settings": {"m1": {"ssh_user": "new-user", "cores": 16}},
    })
    cfg = wf._load_ssh_config()
    assert cfg["host"] == "legacy-host"   # legacy fallback
    assert cfg["user"] == "new-user"      # new map
    assert cfg["cores"] == 16


def test_wrapper_uses_cores_and_paths(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, monkeypatch, {
        "runner_settings": {"m1": {
            "ssh_host": "h", "ssh_user": "u", "cores": 8,
            "snakemake_path": "/opt/bin/snakemake",
            "conda_path": "/opt/conda/bin/conda",
            "remote_workdir": "/remote",
        }},
    })
    wf.ssh_config = wf._load_ssh_config()
    wf.remote_exec_path = "/remote/wf-uuid"
    wf.logger = lambda msg: None

    written = {}

    class FakeSsh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def put_text(self, text, path): written[path] = text
        def exec(self, cmd): return "", "", 0

    with mock.patch.object(SshWorkflow, "_ssh", return_value=FakeSsh()):
        wf._start_remote_snakemake()

    wrapper = written["/remote/wf-uuid/yuki_run.sh"]
    assert "/opt/bin/snakemake --use-conda --cores 8" in wrapper
    assert "/opt/conda/bin" in wrapper  # PATH injection for conda
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest UnitTest/test_ssh_runner_settings.py -v`
Expected: FAIL — wrapper has hardcoded `snakemake --use-conda --cores all`; `cores` missing from config

- [ ] **Step 3: Write minimal implementation**

Replace `_load_ssh_config` body:

```python
    def _load_ssh_config(self):
        """Read SSH connection settings (new map preferred, legacy fallback)."""
        runner_id = self.machine_id or ""
        if not runner_id:
            return {}
        return runner_config.get_ssh_settings(
            runner_config.open_config(), runner_id)
```

(add `from Yuki.kernel import runner_config` at top; the `metadata` import may become unused — remove if so.)

Replace the wrapper template in `_start_remote_snakemake`:

```python
        snakemake_bin = self.ssh_config.get("snakemake_path") or "snakemake"
        cores = self.ssh_config.get("cores", "all")
        conda_path = self.ssh_config.get("conda_path") or ""
        path_export = ""
        if conda_path:
            conda_dir = conda_path.rsplit("/", 1)[0]
            path_export = f'export PATH="{conda_dir}:$PATH"\n'
        wrapper = f'''#!/bin/bash
set -e
{path_export}cd "$(dirname "$0")"
nohup {snakemake_bin} --use-conda --cores {cores} --snakefile Snakefile > snakemake.log 2>&1 &
echo $! > yuki.pid
wait $!
echo $? > yuki.exit
'''
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest UnitTest/test_ssh_runner_settings.py UnitTest/ -v -k "ssh or runner"`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/ssh_workflow.py UnitTest/test_ssh_runner_settings.py
git commit -m "feat(kernel): ssh runner merged config; wrapper honors cores/paths

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Part 2 — Celebi (client, repo `../Celebi`)

### Task 7: ChernCommunicator runner methods + port fallback

**Files:**
- Modify: `CelebiChrono/kernel/chern_communicator.py` (`serverurl` L104–106, `register_runner` L593–611, `runners_url` docstring L562–566; add `test_runner` and `runner_health` after `runner_connection` L653)
- Test: `UnitTest/test_cherncommunicator.py` (append test methods to the existing runner test class)

**Interfaces:**
- Produces:
  - `ChernCommunicator.register_runner(runner, runner_url, token, backend_type, settings=None)` — `settings` dict merged into the POST form; `None` keeps the exact old form (old tests keep passing).
  - `ChernCommunicator.test_runner(runner) -> dict` — GET `/test-runner/<runner>` with `timeout=30`; 404 → `{"status": "unsupported", "message": "DITE server does not support runner testing (upgrade Yuki)"}`; connection failure → raises `ConnectionError`.
  - `ChernCommunicator.runner_health(runner) -> dict` — GET `/runner-health/<runner>`; 404 → `{"status": "untested"}`.
  - `serverurl()` fallback `"127.0.0.1:3315"`.

- [ ] **Step 1: Write the failing test**

```python
    @patch("CelebiChrono.kernel.chern_communicator.requests.post")
    def test_register_runner_with_settings(self, mock_post):
        prepare.create_chern_project("demo_genfit_new")
        os.chdir("demo_genfit_new")
        self.comm = ChernCommunicator()
        self.comm.serverurl = MagicMock(return_value="localhost:8080")

        mock_post.return_value = MagicMock(text="successful")
        self.comm.register_runner("local", "", "", "native",
                                  settings={"workdir": "/data", "cores": 8})
        mock_post.assert_called_once_with(
            "http://localhost:8080/register-runner",
            data={'runner': 'local', 'url': '', 'token': '',
                  'backend_type': 'native', 'workdir': '/data', 'cores': 8},
            timeout=10
        )
        os.chdir("..")
        prepare.remove_chern_project("demo_genfit_new")
        CHERN_CACHE.__init__()

    @patch("CelebiChrono.kernel.chern_communicator.requests.get")
    def test_test_runner(self, mock_get):
        prepare.create_chern_project("demo_genfit_new")
        os.chdir("demo_genfit_new")
        self.comm = ChernCommunicator()
        self.comm.serverurl = MagicMock(return_value="localhost:8080")

        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"status": "ok", "checks": {}}))
        result = self.comm.test_runner("local")
        self.assertEqual(result["status"], "ok")
        mock_get.assert_called_once_with(
            "http://localhost:8080/test-runner/local", timeout=30)

        mock_get.return_value = MagicMock(status_code=404)
        result = self.comm.test_runner("local")
        self.assertEqual(result["status"], "unsupported")
        os.chdir("..")
        prepare.remove_chern_project("demo_genfit_new")
        CHERN_CACHE.__init__()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest UnitTest.test_cherncommunicator -v -k test_test_runner` (or run the two methods by name)
Expected: FAIL — `AttributeError: ... no attribute 'test_runner'`; register asserts mismatch

- [ ] **Step 3: Write minimal implementation**

```python
    def register_runner(self, runner, runner_url, token, backend_type,
                        settings=None):
        """ Register a runner to the server """
        url = self.serverurl()
        data = {'runner': runner, 'url': runner_url, 'token': token,
                'backend_type': backend_type}
        if settings:
            data.update(settings)
        try:
            r = requests.post(f"http://{url}/register-runner",
                              data=data, timeout=self.timeout)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to DITE server: {e}") from e
        if r.text != "successful":
            raise RuntimeError(f"Runner registration failed: {r.text}")
        return True
```

```python
    def test_runner(self, runner):
        """ Ask the server to probe a runner's capabilities (snakemake/conda) """
        url = self.serverurl()
        try:
            r = requests.get(f"http://{url}/test-runner/{runner}", timeout=30)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to DITE server: {e}") from e
        if r.status_code == 404:
            return {"status": "unsupported",
                    "message": "DITE server does not support runner testing "
                               "(upgrade Yuki)"}
        return r.json()

    def runner_health(self, runner):
        """ Get the persisted health of a runner without re-probing """
        url = self.serverurl()
        try:
            r = requests.get(f"http://{url}/runner-health/{runner}",
                             timeout=self.timeout)
        except Exception:
            return {"status": "untested"}
        if r.status_code == 404:
            return {"status": "untested"}
        return r.json()
```

Also: `serverurl()` fallback `"localhost:5000"` → `"127.0.0.1:3315"`; same change in `interface/shell_modules/reana_booking.py` (two occurrences L37–38); delete the "✗ UNUSED METHOD" lines from `runners_url` docstring.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest UnitTest.test_cherncommunicator -v`
Expected: all pass (including pre-existing runner tests — old `register_runner` calls still send the 4-key form)

- [ ] **Step 5: Commit** (in the Celebi repo)

```bash
cd /Users/wave/workdir/Celebi/Celebi
git add CelebiChrono/kernel/chern_communicator.py CelebiChrono/interface/shell_modules/reana_booking.py UnitTest/test_cherncommunicator.py
git commit -m "feat(communicator): test_runner/runner_health, register settings, port fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Shell-layer runner UX (health column, settings kwargs, fixes)

**Files:**
- Modify: `CelebiChrono/interface/shell_modules/communication.py` (`add_host` L15–42, `runners` L141–199, `register_runner` L202–235, `update_runner` L268–307; add `test_runner`)
- Test: `UnitTest/test_shell_runner_management.py` (create, unittest class mocking `ChernCommunicator`)

**Interfaces:**
- Consumes: communicator methods from Task 7.
- Produces:
  - `register_runner(runner, url, secret, backend_type, **kwargs) -> Message` — kwargs forwarded as `settings`.
  - `test_runner(runner) -> Message` — renders the probe result table.
  - `runners()` output gains Health line per runner.
  - After successful `runners()`/`register_runner()`, the name list is written to `~/.celebi/readline.yaml` key `runners` (tab-completion cache).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for shell-layer runner management functions."""
import os
import tempfile
import unittest
from unittest import mock

from CelebiChrono.interface.shell_modules import communication


class TestShellRunnerManagement(unittest.TestCase):

    def _cherncc(self, **attrs):
        cc = mock.MagicMock()
        for key, value in attrs.items():
            setattr(cc, key, value)
        return cc

    def test_register_runner_forwards_settings(self):
        cc = self._cherncc(register_runner=mock.MagicMock(return_value=True))
        with mock.patch.object(communication, "ChernCommunicator") as cls:
            cls.instance.return_value = cc
            communication.register_runner(
                "local", "", "", "native", workdir="/data", cores=8)
        cc.register_runner.assert_called_once_with(
            "local", "", "", "native",
            settings={"workdir": "/data", "cores": 8})

    def test_test_runner_renders_checks(self):
        cc = self._cherncc(test_runner=mock.MagicMock(return_value={
            "status": "failed",
            "checks": {
                "snakemake": {"ok": True, "version": "8.1.0"},
                "conda": {"ok": False, "error": "not found in PATH"},
            }}))
        with mock.patch.object(communication, "ChernCommunicator") as cls:
            cls.instance.return_value = cc
            message = communication.test_runner("local")
        text = str(message)
        self.assertIn("8.1.0", text)
        self.assertIn("not found in PATH", text)

    def test_runners_writes_completion_cache(self):
        tmp = tempfile.mkdtemp()
        cc = self._cherncc(
            dite_status=mock.MagicMock(return_value="connected"),
            runners=mock.MagicMock(return_value=["local"]),
            runners_config=mock.MagicMock(return_value=[{
                "name": "local", "backend_type": "native",
                "settings": {"cores": 8},
                "health": {"status": "ok", "checked_at": "2026-08-13T10:00:00"}}]),
            runners_url=mock.MagicMock(return_value=[""]),
            runner_connection=mock.MagicMock(return_value={"status": "Connected"}),
        )
        with mock.patch.object(communication, "ChernCommunicator") as cls, \
                mock.patch.dict(os.environ, {"HOME": tmp}):
            cls.instance.return_value = cc
            message = communication.runners()
        from CelebiChrono.utils.metadata import YamlFile
        cache = YamlFile(os.path.join(tmp, ".celebi", "readline.yaml"))
        self.assertEqual(cache.read_variable("runners", []), ["local"])
        self.assertIn("ok", str(message))

    def test_add_host_calls_communicator(self):
        cc = self._cherncc()
        with mock.patch.object(communication, "ChernCommunicator") as cls:
            cls.instance.return_value = cc
            communication.add_host("myhost", "127.0.0.1:3315")
        cc.add_host.assert_called_once_with("127.0.0.1:3315")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest UnitTest.test_shell_runner_management -v`
Expected: FAIL — `add_host` arity TypeError; no `test_runner`; no cache write

- [ ] **Step 3: Write minimal implementation**

Fix `add_host` (drop the extra arg):

```python
    cherncc = ChernCommunicator.instance()
    cherncc.add_host(url)
```

New cache helper + `test_runner` in `communication.py`:

```python
def _update_runner_completion_cache(runner_names):
    """Persist runner names for interactive-shell tab completion."""
    cache_path = os.path.join(os.environ["HOME"], ".celebi", "readline.yaml")
    metadata.YamlFile(cache_path).write_variable("runners", runner_names)


def test_runner(runner: str) -> Message:
    """Probe a runner's capabilities (snakemake/conda/workdir) via DITE."""
    message = Message()
    cherncc = ChernCommunicator.instance()
    try:
        result = cherncc.test_runner(runner)
    except ConnectionError as e:
        message.add(str(e), "error")
        return message
    status = result.get("status", "unknown")
    tag = {"ok": "success", "failed": "error"}.get(status, "warning")
    message.add(f"Runner '{runner}': {status}", tag)
    if status == "unsupported":
        message.add(result.get("message", ""), "warning")
        return message
    for name, check in result.get("checks", {}).items():
        if check.get("ok"):
            detail = check.get("version") or check.get("path") or "ok"
            message.add(f"\n  {name:<20}{detail}", "success")
        else:
            message.add(f"\n  {name:<20}{check.get('error', 'failed')}", "error")
    return message
```

`register_runner`: signature → `register_runner(runner, url, secret, backend_type, **kwargs)`; call `cherncc.register_runner(runner, url, secret, backend_type, settings=kwargs or None)`; on success call `_update_runner_completion_cache(cherncc.runners())` inside a try/except (cache failure must not break registration).

`runners()`: after the existing `message.add(f"{'Status: ':<20}"...)` block, add health rendering:

```python
            health = cfg.get("health", {})
            health_status = health.get("status", "untested")
            health_tag = {"ok": "success", "failed": "error"}.get(
                health_status, "normal")
            checked_at = health.get("checked_at", "")
            suffix = f" ({checked_at})" if checked_at else ""
            message.add(f"{'Health: ':<20}{health_status}{suffix}\n", health_tag)
            settings = cfg.get("settings", {})
            if settings.get("workdir"):
                message.add(f"{'Workdir: ':<20}{settings['workdir']}\n", "normal")
            if settings.get("cores"):
                message.add(f"{'Cores: ':<20}{settings['cores']}\n", "normal")
```

and at the end (before `return message`): `_update_runner_completion_cache(runner_list)` guarded by try/except.

`update_runner` docstring: add the new keys (`workdir`, `cores`, `mem_mb`, `conda_path`, `snakemake_path`, `ssh_host`, `ssh_user`, `ssh_key_path`, `ssh_port`, `remote_workdir`) — the `**kwargs` pass-through already handles them.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest UnitTest.test_shell_runner_management -v`
Expected: 4 passed

- [ ] **Step 5: Commit** (Celebi repo)

```bash
git add CelebiChrono/interface/shell_modules/communication.py UnitTest/test_shell_runner_management.py
git commit -m "feat(shell): runner health display, settings kwargs, completion cache

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: `celebi-cli` runner commands

**Files:**
- Modify: `CelebiChrono/celebi_cli/commands/execution_management.py` (`register_runner_command` L69–98, `update_runner_command` L127–158; add `test_runner_command`)
- Modify: `CelebiChrono/celebi_cli/cli.py` (register `test_runner_command` after L67)
- Test: `UnitTest/test_cli_runner_commands.py` (create, click `CliRunner` mocking the shell functions)

**Interfaces:**
- Consumes: shell functions from Task 8 (`register_runner(..., **kwargs)`, `update_runner(runner, **kwargs)`, `test_runner(runner)`).
- Produces: CLI surface —
  - `celebi-cli register-runner NAME URL SECRET BACKEND_TYPE [--ssh-host --ssh-user --ssh-key-path --ssh-port --remote-workdir --workdir --cores --mem-mb --conda-path --snakemake-path]`
  - `celebi-cli update-runner NAME [<same options plus existing ones>]`
  - `celebi-cli test-runner NAME`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for celebi-cli runner commands."""
import unittest
from unittest import mock

from click.testing import CliRunner

from CelebiChrono.celebi_cli.commands.execution_management import (
    register_runner_command, test_runner_command, update_runner_command,
)


class TestCliRunnerCommands(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    def test_register_runner_passes_settings(self):
        with mock.patch("CelebiChrono.interface.shell.register_runner") as fn:
            result = self.runner.invoke(register_runner_command, [
                "local", "", "", "native",
                "--workdir", "/data", "--cores", "8",
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("local", "", "", "native",
                                   workdir="/data", cores=8)

    def test_register_runner_ssh_options(self):
        with mock.patch("CelebiChrono.interface.shell.register_runner") as fn:
            result = self.runner.invoke(register_runner_command, [
                "cluster", "", "", "ssh",
                "--ssh-host", "h", "--ssh-user", "u", "--ssh-port", "2222",
                "--remote-workdir", "/remote",
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("cluster", "", "", "ssh",
                                   ssh_host="h", ssh_user="u", ssh_port=2222,
                                   remote_workdir="/remote")

    def test_update_runner_passes_settings(self):
        with mock.patch("CelebiChrono.interface.shell.update_runner") as fn:
            result = self.runner.invoke(update_runner_command, [
                "local", "--cores", "16", "--conda-path", "/opt/conda/bin/conda",
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("local", cores=16,
                                   conda_path="/opt/conda/bin/conda")

    def test_test_runner_command(self):
        with mock.patch("CelebiChrono.interface.shell.test_runner") as fn:
            result = self.runner.invoke(test_runner_command, ["local"])
        self.assertEqual(result.exit_code, 0, result.output)
        fn.assert_called_once_with("local")
```

Note: the shell functions are imported lazily inside each command (`from CelebiChrono.interface.shell import ...`), so patching `CelebiChrono.interface.shell.<name>` works. `test_runner` must be re-exported in `CelebiChrono/interface/shell.py` alongside the other communication functions (add it to the import list there).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest UnitTest.test_cli_runner_commands -v`
Expected: FAIL — no such options / `ImportError: cannot import name 'test_runner_command'`

- [ ] **Step 3: Write minimal implementation**

Shared option decorator at top of `execution_management.py` (after imports):

```python
_RUNNER_SETTING_OPTIONS = [
    click.option("--ssh-host", type=str, default=None, help="SSH host (ssh backend)"),
    click.option("--ssh-user", type=str, default=None, help="SSH user (ssh backend)"),
    click.option("--ssh-key-path", type=str, default=None, help="SSH private key path"),
    click.option("--ssh-port", type=int, default=None, help="SSH port"),
    click.option("--remote-workdir", type=str, default=None,
                 help="Remote working directory (ssh backend)"),
    click.option("--workdir", type=str, default=None,
                 help="Local working directory (native backend)"),
    click.option("--cores", type=int, default=None, help="Snakemake cores"),
    click.option("--mem-mb", type=int, default=None, help="Snakemake memory (MB)"),
    click.option("--conda-path", type=str, default=None,
                 help="Path to conda executable"),
    click.option("--snakemake-path", type=str, default=None,
                 help="Path to snakemake executable"),
]

_SETTING_KEYS = ("ssh_host", "ssh_user", "ssh_key_path", "ssh_port",
                 "remote_workdir", "workdir", "cores", "mem_mb",
                 "conda_path", "snakemake_path")


def _runner_setting_options(func):
    for option in reversed(_RUNNER_SETTING_OPTIONS):
        func = option(func)
    return func


def _collect_cli_settings(kwargs):
    return {key: kwargs[key] for key in _SETTING_KEYS
            if kwargs.get(key) is not None}
```

Apply to `register_runner_command`:

```python
@click.command(name="register-runner")
@click.argument("name", type=str)
@click.argument("url", type=str)
@click.argument("secret", type=str)
@click.argument("backend_type", type=str)
@_runner_setting_options
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def register_runner_command(name, url, secret, backend_type, **kwargs):
    """Register a new runner with DITE. ... (extend docstring with options) ..."""
    try:
        from CelebiChrono.interface.shell import register_runner
        _handle_result(register_runner(name, url, secret, backend_type,
                                       **_collect_cli_settings(kwargs)))
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")
```

Apply to `update_runner_command`: add `@_runner_setting_options`, accept `**kwargs`, and merge `_collect_cli_settings(kwargs)` into `settings` before calling.

New command:

```python
@click.command(name="test-runner")
@click.argument("runner", type=str)
def test_runner_command(runner: str) -> None:
    """Probe a runner's capabilities (snakemake/conda/workdir) via DITE.

    RUNNER is the name of the registered runner to test. Results are stored
    on the server and shown in 'celebi-cli runners'.
    """
    try:
        from CelebiChrono.interface.shell import test_runner
        _handle_result(test_runner(runner))
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")
```

`cli.py`: add `cli.add_command(execution_management.test_runner_command)` after the `remove_runner_command` line.

`CelebiChrono/interface/shell.py`: add `test_runner` to the names imported from `shell_modules.communication` (mirror the existing `register_runner` import line).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest UnitTest.test_cli_runner_commands -v`
Expected: 4 passed. Then smoke check: `celebi-cli register-runner --help` lists the new options; `celebi-cli test-runner --help` works.

- [ ] **Step 5: Commit** (Celebi repo)

```bash
git add CelebiChrono/celebi_cli/commands/execution_management.py CelebiChrono/celebi_cli/cli.py CelebiChrono/interface/shell.py UnitTest/test_cli_runner_commands.py
git commit -m "feat(cli): test-runner command and runner settings options

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Full test suites + lint + manual e2e

**Files:** none (verification only)

- [ ] **Step 1: Yuki full suite**

Run: `cd /Users/wave/workdir/Celebi/Yuki && python -m pytest UnitTest/ -v`
Expected: all pass

- [ ] **Step 2: Celebi full suite**

Run: `cd /Users/wave/workdir/Celebi/Celebi && python -m unittest discover UnitTest -v` (or the repo's standard runner)
Expected: all pass

- [ ] **Step 3: Pylint both repos**

Run (each repo): `pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" $(git ls-files '*.py')`
Expected: no new warnings on changed files

- [ ] **Step 4: Manual end-to-end (documented in the final report, not automated)**

```bash
cd /Users/wave/workdir/Celebi/Yuki && docker compose up -d
# in another shell, from a Celebi demo project:
celebi-cli register-runner local "" "" native --workdir /tmp/yuki-e2e --cores 4
celebi-cli test-runner local        # expect snakemake/conda/workdir checks
celebi-cli runners                  # expect Health column shows ok
celebi-cli update-runner local --cores 8
celebi-cli remove-runner local      # expect config cleaned
```

Expected: each command succeeds; `test-runner` output lists per-check results; failures (e.g. conda missing in the container) render in red with a remediation hint.

- [ ] **Step 5: Fix anything the e2e surfaces, then done — no commit needed unless fixes were made**
