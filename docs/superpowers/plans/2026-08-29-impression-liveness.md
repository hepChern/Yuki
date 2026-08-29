# Impression Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Realize the two commands — `celebi-cli purge-stale-cache <runner>` (purge superseded impressions' cache entries on a runner) and `celebi-cli purge-stale-workflows <runner>` (delete non-live workflow workspaces on a runner) — on top of a live-set registry synced from Celebi to Yuki.

**Architecture:** Celebi computes the project's live (current task/algorithm versions + transitive inputs) and superseded (history, non-current) impression sets and PUTs them to `Yuki` via a new `PUT /live-set/<project>` endpoint. Yuki stores `.Yuki/Live/<project>.json`, derives live workflows from run configs, and uses the set in two purge paths: a `superseded` scope on the existing `purge-runner-cache`, and a new `purge_stale_workflows` kernel function + `POST /purge-runner-workflows` route (mirror scan → per-backend `delete_workspace`). Unknown = live: a stale or missing set can only miss GC opportunities, never destroy a current version.

**Tech Stack:** Python, Flask blueprints, pytest + unittest.mock, click (celebi-cli), requests (chern_communicator), CelebiChrono `metadata.ConfigFile`.

**Spec:** `docs/superpowers/specs/2026-08-29-impression-liveness-design.md`

**Repo paths:** Yuki work happens in `/Users/wave/workdir/Celebi/Yuki` (work on `main`, user-consented in-place work). CelebiChrono work happens in its repo root `/Users/wave/workdir/Celebi/Celebi` (package sources under `CelebiChrono/`, tests under `UnitTest/` and `tests/` — commit there directly).

## Global Constraints

- Yuki test command: `python -m pytest UnitTest/ -q` (baseline: 397 passing). CelebiChrono test command: `python -m pytest UnitTest/ tests/ -q` run from `/Users/wave/workdir/Celebi/Celebi`.
- Yuki pylint on changed files: `pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" <files>`.
- Unknown is live: only impressions in the explicit `superseded` list are ever auto-GC-able; `workflow_live` returns None when the project has no synced set, and every purge path skips None.
- `PUT /live-set` must be idempotent and atomic (tmp + `os.replace`); a validation or derivation failure must store nothing.
- The live registry lives under `.Yuki/Live/` — never inside `.Yuki/Bookkeep/` (`/bookkeeping` wipes that dir).
- No new dependencies in either repo. No background scheduler.
- Route house style: `jsonify({"error": str(e)})` with 400/404/409/500; destructive routes follow existing purge patterns (confirmation prompt in the CLI unless `--yes`, `--dry-run` lists).
- Existing Yuki tests (397) and CelebiChrono tests must stay green at every commit.

---

## Yuki side

### Task 1: `Yuki/kernel/liveness.py` — the live registry

**Files:**
- Create: `Yuki/kernel/liveness.py`
- Test: `UnitTest/test_liveness.py` (new)

**Interfaces:**
- Produces (used by later tasks):
  - `save_live_set(project_uuid, live, superseded, yuki_dir=None) -> dict` — raises `ValueError` naming invalid entries; returns `{"stored": True, "live": n, "superseded": n, "live_workflows": n}`.
  - `load_live_set(project_uuid, yuki_dir=None) -> dict | None`.
  - `impression_live(project_uuid, impression, yuki_dir=None) -> True | False | None`.
  - `workflow_live(project_uuid, workflow_uuid, yuki_dir=None) -> True | False | None`.
  - `live_path(yuki_dir, project_uuid) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `UnitTest/test_liveness.py`:

```python
"""Tests for the live-set registry (kernel/liveness.py)."""
import json
import os
from unittest import mock

from Yuki.kernel import liveness


def _write_run_config(tmp_path, project, impression, machine, workflow):
    """Write a per-machine run config with a workflow id."""
    run_dir = tmp_path / "Storage" / project / impression / machine
    run_dir.mkdir(parents=True)
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"workflow": workflow}, f)


def test_save_and_load_round_trip(monkeypatch, tmp_path):
    """save_live_set persists live, superseded, and derived workflows."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    _write_run_config(tmp_path, "proj", "a" * 32, "r1", "wf-1")
    _write_run_config(tmp_path, "proj", "b" * 32, "r1", "wf-2")

    summary = liveness.save_live_set("proj", ["a" * 32], ["c" * 32])

    assert summary == {"stored": True, "live": 1, "superseded": 1,
                       "live_workflows": 1}
    data = liveness.load_live_set("proj")
    assert data["live"] == ["a" * 32]
    assert data["superseded"] == ["c" * 32]
    assert data["live_workflows"] == ["wf-1"]
    assert "updated" in data
    # The file lives under .Yuki/Live/, not Bookkeep.
    assert os.path.isfile(
        tmp_path / "Live" / "proj.json")


def test_save_live_set_rejects_invalid_entries(monkeypatch, tmp_path):
    """Invalid uuids and uuids in both lists are rejected; nothing stored."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    with __import__("pytest").raises(ValueError) as exc:
        liveness.save_live_set("proj", ["not-a-uuid"], [])
    assert "not-a-uuid" in str(exc.value)
    assert not os.path.exists(tmp_path / "Live" / "proj.json")

    with __import__("pytest").raises(ValueError) as exc:
        liveness.save_live_set("proj", ["a" * 32], ["a" * 32])
    assert "both" in str(exc.value)
    assert not os.path.exists(tmp_path / "Live" / "proj.json")


def test_impression_live_semantics(monkeypatch, tmp_path):
    """Explicitly superseded -> False; everything else -> True; no set -> None."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.impression_live("proj", "a" * 32) is None
    liveness.save_live_set("proj", ["a" * 32], ["b" * 32])
    assert liveness.impression_live("proj", "a" * 32) is True
    assert liveness.impression_live("proj", "b" * 32) is False
    # Unknown-to-the-list is treated live (conservative).
    assert liveness.impression_live("proj", "c" * 32) is True


def test_workflow_live_semantics(monkeypatch, tmp_path):
    """Membership in derived live_workflows; None without a set."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.workflow_live("proj", "wf-1") is None
    _write_run_config(tmp_path, "proj", "a" * 32, "r1", "wf-1")
    liveness.save_live_set("proj", ["a" * 32], [])
    assert liveness.workflow_live("proj", "wf-1") is True
    assert liveness.workflow_live("proj", "wf-9") is False


def test_load_live_set_missing_or_corrupt(monkeypatch, tmp_path):
    """A missing or corrupt file loads as None."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    assert liveness.load_live_set("proj") is None
    live_dir = tmp_path / "Live"
    live_dir.mkdir()
    with open(live_dir / "proj.json", "w", encoding="utf-8") as f:
        f.write("{not json")
    assert liveness.load_live_set("proj") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_liveness.py -q`
Expected: 5 errors — `ModuleNotFoundError: No module named 'Yuki.kernel.liveness'`

- [ ] **Step 3: Write minimal implementation**

Create `Yuki/kernel/liveness.py`:

```python
"""Live-set registry: which impressions are the current versions of
tasks/algorithms, and which workflows their runs produced."""
import datetime
import json
import os
import re

UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def _yuki_dir():
    """Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def live_path(yuki_dir, project_uuid):
    """Path of a project's live-set file."""
    return os.path.join(yuki_dir, "Live", f"{project_uuid}.json")


def validate_sets(live, superseded):
    """Raise ValueError naming any entry that is not a 32-hex uuid or
    appears in both lists."""
    problems = []
    seen_live, seen_sup = set(), set()
    for uuid in live:
        if not isinstance(uuid, str) or not UUID_RE.match(uuid):
            problems.append(f"invalid live entry: {uuid!r}")
        seen_live.add(uuid)
    for uuid in superseded:
        if not isinstance(uuid, str) or not UUID_RE.match(uuid):
            problems.append(f"invalid superseded entry: {uuid!r}")
        seen_sup.add(uuid)
    for uuid in seen_live & seen_sup:
        problems.append(f"uuid in both lists: {uuid}")
    if problems:
        raise ValueError("; ".join(problems))


def derive_live_workflows(project_uuid, live_impressions, yuki_dir=None):
    """Workflow uuids from the per-machine run configs of live impressions."""
    yuki_dir = yuki_dir or _yuki_dir()
    from CelebiChrono.utils.metadata import ConfigFile
    storage = os.path.join(yuki_dir, "Storage", project_uuid)
    workflows = set()
    if not os.path.isdir(storage):
        return sorted(workflows)
    for impression in live_impressions:
        imp_dir = os.path.join(storage, impression)
        if not os.path.isdir(imp_dir):
            continue
        for machine in os.listdir(imp_dir):
            run_cfg = os.path.join(imp_dir, machine, "config.json")
            if not os.path.isfile(run_cfg):
                continue
            workflow = ConfigFile(run_cfg).read_variable("workflow", "")
            if workflow:
                workflows.add(workflow)
    return sorted(workflows)


def save_live_set(project_uuid, live, superseded, yuki_dir=None):
    """Validate, derive live workflows, and atomically replace the set.

    Returns {"stored": True, "live": n, "superseded": n,
    "live_workflows": n}. Raises ValueError on invalid input; anything
    else propagates (nothing is stored on failure).
    """
    yuki_dir = yuki_dir or _yuki_dir()
    validate_sets(live, superseded)
    live = sorted(set(live))
    superseded = sorted(set(superseded))
    live_workflows = derive_live_workflows(project_uuid, live, yuki_dir)
    payload = {
        "live": live,
        "superseded": superseded,
        "live_workflows": live_workflows,
        "updated": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    path = live_path(yuki_dir, project_uuid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp_path, path)
    return {"stored": True, "live": len(live),
            "superseded": len(superseded),
            "live_workflows": len(live_workflows)}


def load_live_set(project_uuid, yuki_dir=None):
    """The stored set, or None."""
    yuki_dir = yuki_dir or _yuki_dir()
    path = live_path(yuki_dir, project_uuid)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def impression_live(project_uuid, impression, yuki_dir=None):
    """True (live) / False (explicitly superseded) / None (no set)."""
    data = load_live_set(project_uuid, yuki_dir)
    if data is None:
        return None
    if impression in data.get("superseded", []):
        return False
    return True


def workflow_live(project_uuid, workflow_uuid, yuki_dir=None):
    """True (derived live workflow) / False / None (no set)."""
    data = load_live_set(project_uuid, yuki_dir)
    if data is None:
        return None
    return workflow_uuid in data.get("live_workflows", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_liveness.py -q`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/liveness.py UnitTest/test_liveness.py
git commit -m "feat(kernel): live-set registry with workflow derivation"
```

---

### Task 2: `PUT /live-set` + `GET /live` blueprint

**Files:**
- Create: `Yuki/server/routes/liveness.py`
- Modify: `Yuki/server/app.py` (import + register blueprint)
- Test: `UnitTest/test_liveness.py` (append)

**Interfaces:**
- Consumes: `liveness.save_live_set`, `liveness.load_live_set` (Task 1).
- Produces: `PUT /live-set/<project_uuid>` (200/400/500) and `GET /live/<project_uuid>` (200/404) under blueprint name `liveness`.

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_liveness.py`:

```python
def _app():
    from Yuki.server.routes import liveness as liveness_routes
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(liveness_routes.bp)
    return app


def test_put_live_set_stores_and_reports(monkeypatch, tmp_path):
    """/live-set stores the set and returns the summary."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    r = _app().test_client().put(
        "/live-set/proj",
        json={"live": ["a" * 32], "superseded": ["b" * 32]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["stored"] is True
    assert body["live"] == 1
    assert body["superseded"] == 1
    assert os.path.isfile(tmp_path / "Live" / "proj.json")


def test_put_live_set_invalid_400_nothing_stored(monkeypatch, tmp_path):
    """Invalid input returns 400 and stores nothing."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    r = _app().test_client().put(
        "/live-set/proj", json={"live": ["nope"], "superseded": []})
    assert r.status_code == 400
    assert "nope" in r.get_json()["error"]
    assert not os.path.exists(tmp_path / "Live" / "proj.json")


def test_get_live_returns_stored_set(monkeypatch, tmp_path):
    """/live returns the stored set with derived workflows."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    _write_run_config(tmp_path, "proj", "a" * 32, "r1", "wf-1")
    _app().test_client().put(
        "/live-set/proj",
        json={"live": ["a" * 32], "superseded": []})
    r = _app().test_client().get("/live/proj")
    assert r.status_code == 200
    body = r.get_json()
    assert body["live_impressions"] == ["a" * 32]
    assert body["live_workflows"] == ["wf-1"]
    assert body["superseded"] == []


def test_get_live_unknown_project_404(monkeypatch, tmp_path):
    """/live without a synced set returns 404."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    r = _app().test_client().get("/live/proj")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_liveness.py -q`
Expected: 4 errors — `ModuleNotFoundError: No module named 'Yuki.server.routes.liveness'`

- [ ] **Step 3: Write minimal implementation**

Create `Yuki/server/routes/liveness.py`:

```python
"""
Live-set sync routes: Celebi pushes which impressions are the current
versions of tasks/algorithms; Yuki stores and serves the set.
"""
from flask import Blueprint, jsonify, request

from ...kernel import liveness

bp = Blueprint('liveness', __name__)


@bp.route("/live-set/<project_uuid>", methods=['PUT'])
def put_live_set(project_uuid):
    """Replace the project's live set (idempotent full-state sync)."""
    data = request.get_json(silent=True) or {}
    live = data.get("live") or []
    superseded = data.get("superseded") or []
    try:
        summary = liveness.save_live_set(project_uuid, live, superseded)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # pylint: disable=broad-exception-caught
        return jsonify({"error": str(e)}), 500
    return jsonify(summary)


@bp.route("/live/<project_uuid>", methods=['GET'])
def get_live(project_uuid):
    """The stored live set, or 404 when none has been synced."""
    data = liveness.load_live_set(project_uuid)
    if data is None:
        return jsonify({"error": f"no live set for project "
                                 f"'{project_uuid}'"}), 404
    return jsonify({
        "live_impressions": data.get("live", []),
        "live_workflows": data.get("live_workflows", []),
        "superseded": data.get("superseded", []),
        "updated": data.get("updated", ""),
    })
```

Modify `Yuki/server/app.py` — add `liveness` to the route import and register it:

```python
from .routes import (
    upload, execution, status, runner, workflow,
    transfer, impression, booking, remote_data, liveness,
)
```

and after `flask_app.register_blueprint(remote_data.bp)`:

```python
    flask_app.register_blueprint(liveness.bp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_liveness.py UnitTest/test_server.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/liveness.py Yuki/server/app.py UnitTest/test_liveness.py
git commit -m "feat(server): live-set sync routes (PUT /live-set, GET /live)"
```

---

### Task 3: `purge-runner-cache` superseded scope

**Files:**
- Modify: `Yuki/kernel/remote_data_ops.py` (`purge_runner_cache` signature + walk filter + import `liveness`)
- Modify: `Yuki/server/routes/remote_data.py` (purge route passes `superseded`, validates it is not combined with project/impression)
- Test: `UnitTest/test_purge_runner_cache.py` (append)

**Interfaces:**
- Consumes: `liveness.impression_live` (Task 1).
- Produces: `purge_runner_cache(runner_id, project=None, impression=None, dry_run=False, echo=None, yuki_dir=None, superseded=False)`.

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_purge_runner_cache.py` (reusing the file's existing `_FakeSsh` and `_write_runner_config` helpers):

```python
def test_purge_runner_cache_superseded_scope(tmp_path):
    """superseded=True selects only explicitly-superseded impressions."""
    from Yuki.kernel import liveness
    _write_runner_config(tmp_path)
    live_a, old_b = "a" * 32, "b" * 32
    liveness.save_live_set("proj1", [live_a], [old_b],
                           yuki_dir=str(tmp_path))
    fake = _FakeSsh(tree={"remote": {"work": {"impressions": {
        "proj1": {live_a: {}, old_b: {}},
    }}}})
    with mock.patch("Yuki.kernel.remote_data_ops._ssh_connection",
                    return_value=fake):
        summary = purge_runner_cache("r1", superseded=True, dry_run=True,
                                     yuki_dir=str(tmp_path))

    assert {e["impression"] for e in summary["purged"]} == {old_b}
    assert summary["dry_run"] is True
    assert not any("rm -rf" in c for c in fake.exec_calls)


def test_purge_superseded_never_touches_unknown(tmp_path):
    """Impressions without a synced set are skipped (unknown is live)."""
    _write_runner_config(tmp_path)
    live_a = "a" * 32
    fake = _FakeSsh(tree={"remote": {"work": {"impressions": {
        "proj1": {live_a: {}},
    }}}})
    with mock.patch("Yuki.kernel.remote_data_ops._ssh_connection",
                    return_value=fake):
        summary = purge_runner_cache("r1", superseded=True, dry_run=True,
                                     yuki_dir=str(tmp_path))
    assert summary["purged"] == []


def _purge_route_app(monkeypatch, tmp_path):
    """A Flask app with the remote_data blueprint and a temp config."""
    from Yuki.server.routes import remote_data as remote_data_routes
    from CelebiChrono.utils.metadata import ConfigFile
    from flask import Flask
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    app = Flask(__name__)
    app.register_blueprint(remote_data_routes.bp)
    config_obj = mock.MagicMock()
    config_obj.config_path = str(tmp_path / "config.json")
    config_obj.get_config_file.return_value = ConfigFile(
        config_obj.config_path)
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump({"runners_id": {"farm": "r1"},
                   "backend_types": {"r1": "ssh"}}, f)
    monkeypatch.setattr(remote_data_routes, "config", config_obj)
    return app


def test_purge_route_superseded_with_filters_400(monkeypatch, tmp_path):
    """superseded combined with project/impression filters is rejected."""
    r = _purge_route_app(monkeypatch, tmp_path).test_client().post(
        "/purge-runner-cache",
        json={"runner": "farm", "superseded": True, "project": "p1"})
    assert r.status_code == 400
    assert "cannot be combined" in r.get_json()["error"]


def test_purge_route_superseded_passes_through(monkeypatch, tmp_path):
    """The route forwards superseded and dry_run to the kernel purge."""
    from Yuki.server.routes import remote_data as remote_data_routes
    app = _purge_route_app(monkeypatch, tmp_path)
    with mock.patch.object(remote_data_routes.remote_data_ops,
                           "purge_runner_cache",
                           return_value={"purged": [], "skipped": [],
                                         "dry_run": True}) as purge:
        r = app.test_client().post(
            "/purge-runner-cache",
            json={"runner": "farm", "superseded": True, "dry_run": True})
    assert r.status_code == 200
    assert purge.call_args[1]["superseded"] is True
    assert purge.call_args[1]["dry_run"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_purge_runner_cache.py -q`
Expected: FAIL — `TypeError: purge_runner_cache() got an unexpected keyword argument 'superseded'` (and the route test returns 200 instead of 400).

- [ ] **Step 3: Write minimal implementation**

In `Yuki/kernel/remote_data_ops.py`, change the signature:

```python
def purge_runner_cache(runner_id, project=None, impression=None,  # pylint: disable=too-many-locals,too-many-branches,too-many-arguments,too-many-positional-arguments
                       dry_run=False, echo=None, yuki_dir=None,
                       superseded=False):
    """Evict cache entries from an ssh runner's impressions cache.

    With superseded=True, only cache entries whose impressions are
    explicitly marked superseded in the project's live set are selected
    (project/impression filters must not be set). Deletes matching
    ``<remote_workdir>/impressions/<project>/<impression>`` directories
    (chmod'd writable first; cached data is stored read-only) and clears
    the local bookkeeping that pointed at them.
    """
```

Add the import at the top of the file (after the existing imports):

```python
from . import liveness
```

Inside the walk, right after `imp_local = ...` / before the running check, add the superseded filter:

```python
                if superseded:
                    live = liveness.impression_live(
                        proj, imp, yuki_dir)
                    if live is not False:
                        continue
```

In `Yuki/server/routes/remote_data.py` `purge_runner_cache_route`, after reading `runner` and before `dry_run`:

```python
    superseded = str(data.get("superseded", "")).lower() in (
        "1", "true", "yes")
    if superseded and (data.get("project") or data.get("impression")):
        return jsonify({"error": "superseded scope cannot be combined "
                                 "with project/impression filters"}), 400
```

and pass it through:

```python
        summary = remote_data_ops.purge_runner_cache(
            runner_id,
            project=data.get("project") or None,
            impression=data.get("impression") or None,
            dry_run=dry_run,
            superseded=superseded,
            yuki_dir=remote_data_ops._yuki_dir())  # pylint: disable=protected-access
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_purge_runner_cache.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/remote_data_ops.py Yuki/server/routes/remote_data.py UnitTest/test_purge_runner_cache.py
git commit -m "feat(server): purge-runner-cache superseded scope"
```

---

### Task 4: `Yuki/kernel/workflow_purge.py`

**Files:**
- Create: `Yuki/kernel/workflow_purge.py`
- Test: `UnitTest/test_workflow_purge.py` (new)

**Interfaces:**
- Consumes: `liveness.workflow_live` (Task 1), `VWorkflow.create` + per-backend `delete_workspace()` (already landed on main), `translate_to_musical`/`IN_MOVEMENT` from `status_constants`.
- Produces: `purge_stale_workflows(runner_id, dry_run=False, yuki_dir=None) -> {"purged": [...], "skipped": [...], "dry_run": bool}`.

- [ ] **Step 1: Write the failing tests**

Create `UnitTest/test_workflow_purge.py`:

```python
"""Tests for stale-workflow workspace purging."""
import json
import os
from unittest import mock

from Yuki.kernel import liveness
from Yuki.kernel import workflow_purge


def _workflow_mirror(tmp_path, project, workflow, machine_id,
                     status="finished"):
    """A Workflows mirror dir with config.json machine_id and results.json."""
    wf_dir = tmp_path / "Workflows" / project / workflow
    wf_dir.mkdir(parents=True)
    with open(wf_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"machine_id": machine_id}, f)
    with open(wf_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump({"results": {"status": status}}, f)
    return wf_dir


def test_purge_stale_workflows_deletes_non_live(monkeypatch, tmp_path):
    """Non-live workflows of the runner are deleted; others are skipped."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", ["a" * 32], [])
    # live impression a -> wf-live; run config per machine.
    run_dir = tmp_path / "Storage" / "proj" / ("a" * 32) / "r1"
    run_dir.mkdir(parents=True)
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({"workflow": "wf-live"}, f)

    _workflow_mirror(tmp_path, "proj", "wf-live", "r1")
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")
    _workflow_mirror(tmp_path, "proj", "wf-other-runner", "r9")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == [
        {"project": "proj", "workflow": "wf-stale"}]
    skipped = {(s["workflow"], s["reason"]) for s in summary["skipped"]}
    assert ("wf-live", "workflow is live") in skipped
    assert ("wf-other-runner", None) not in skipped  # filtered by runner
    fake_workflow.delete_workspace.assert_called_once_with()


def test_purge_stale_workflows_skips_running(monkeypatch, tmp_path):
    """Running workflows are skipped, never deleted."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1", status="running")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "running"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert summary["skipped"][0]["reason"] == "workflow is running"
    fake_workflow.delete_workspace.assert_not_called()


def test_purge_stale_workflows_without_live_set_skips_all(monkeypatch,
                                                          tmp_path):
    """Projects without a synced set are unknown: nothing is purged."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    _workflow_mirror(tmp_path, "proj", "wf-1", "r1")

    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert summary["skipped"][0]["reason"] == \
        "no live set synced for project"
    vwf.create.assert_not_called()


def test_purge_stale_workflows_dry_run(monkeypatch, tmp_path):
    """Dry-run lists what would go and deletes nothing."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1", dry_run=True)

    assert summary["purged"] == [
        {"project": "proj", "workflow": "wf-stale"}]
    assert summary["dry_run"] is True
    fake_workflow.delete_workspace.assert_not_called()


def test_purge_stale_workflows_delete_failure_is_skip(monkeypatch,
                                                      tmp_path):
    """A delete failure becomes a skip entry, not an abort."""
    monkeypatch.setenv("YUKIDIR", str(tmp_path))
    liveness.save_live_set("proj", [], [])
    _workflow_mirror(tmp_path, "proj", "wf-stale", "r1")

    fake_workflow = mock.MagicMock()
    fake_workflow.status.return_value = "finished"
    fake_workflow.delete_workspace.side_effect = OSError("ssh down")
    with mock.patch.object(workflow_purge, "VWorkflow") as vwf:
        vwf.create.return_value = fake_workflow
        summary = workflow_purge.purge_stale_workflows("r1")

    assert summary["purged"] == []
    assert "delete failed" in summary["skipped"][0]["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_workflow_purge.py -q`
Expected: 5 errors — `ModuleNotFoundError: No module named 'Yuki.kernel.workflow_purge'`

- [ ] **Step 3: Write minimal implementation**

Create `Yuki/kernel/workflow_purge.py`:

```python
"""Purge non-live workflow workspaces from a runner."""
import os

from CelebiChrono.utils.metadata import ConfigFile

from . import liveness
from .status_constants import IN_MOVEMENT, translate_to_musical
from .vworkflow import VWorkflow


def purge_stale_workflows(runner_id, dry_run=False, yuki_dir=None):
    """Delete the runner-side workspaces of workflows whose projects'
    synced live sets exclude them.

    Workflows are found in the local mirror
    ~/.Yuki/Workflows/<project>/<workflow> where config.json machine_id
    equals runner_id (covers ssh, native, and reana uniformly). Live
    workflows, running workflows, and workflows without an explicitly
    synced set are skipped with a reason. The mirror is always kept.

    Returns {"purged": [...], "skipped": [...], "dry_run": bool}.
    """
    yuki_dir = yuki_dir or liveness._yuki_dir()  # pylint: disable=protected-access
    workflows_root = os.path.join(yuki_dir, "Workflows")
    purged, skipped = [], []
    if not os.path.isdir(workflows_root):
        return {"purged": purged, "skipped": skipped,
                "dry_run": bool(dry_run)}

    for project in sorted(os.listdir(workflows_root)):
        project_dir = os.path.join(workflows_root, project)
        if not os.path.isdir(project_dir):
            continue
        for workflow_uuid in sorted(os.listdir(project_dir)):
            workflow_dir = os.path.join(project_dir, workflow_uuid)
            if not os.path.isdir(workflow_dir):
                continue
            workflow_config = ConfigFile(
                os.path.join(workflow_dir, "config.json"))
            if workflow_config.read_variable("machine_id", "") != runner_id:
                continue
            entry = {"project": project, "workflow": workflow_uuid}
            live = liveness.workflow_live(project, workflow_uuid, yuki_dir)
            if live is True:
                skipped.append({**entry, "reason": "workflow is live"})
                continue
            if live is None:
                skipped.append({**entry,
                                "reason": "no live set synced for project"})
                continue
            workflow = VWorkflow.create(project, [], workflow_uuid)
            if translate_to_musical(workflow.status()) == IN_MOVEMENT:
                skipped.append({**entry,
                                "reason": "workflow is running"})
                continue
            if dry_run:
                purged.append(entry)
                continue
            try:
                workflow.delete_workspace()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                skipped.append({**entry,
                                "reason": f"delete failed: {exc}"})
                continue
            purged.append(entry)
    return {"purged": purged, "skipped": skipped,
            "dry_run": bool(dry_run)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_workflow_purge.py -q`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/kernel/workflow_purge.py UnitTest/test_workflow_purge.py
git commit -m "feat(kernel): purge non-live workflow workspaces per runner"
```

---

### Task 5: `POST /purge-runner-workflows` route

**Files:**
- Modify: `Yuki/server/routes/workflow.py` (imports + route)
- Test: `UnitTest/test_workflow_purge.py` (append)

**Interfaces:**
- Consumes: `workflow_purge.purge_stale_workflows` (Task 4), `..config.config`.
- Produces: `POST /purge-runner-workflows` (200 summary / 400 / 404 / 500).

- [ ] **Step 1: Write the failing tests**

Append to `UnitTest/test_workflow_purge.py`:

```python
def _purge_app(monkeypatch, config_vars):
    from Yuki.server.routes import workflow as workflow_routes
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(workflow_routes.bp)
    config_obj = mock.MagicMock()
    from CelebiChrono.utils.metadata import ConfigFile
    import tempfile
    tmp = tempfile.mkdtemp()
    config_obj.config_path = os.path.join(tmp, "config.json")
    config_obj.get_config_file.return_value = ConfigFile(
        config_obj.config_path)
    with open(config_obj.config_path, "w", encoding="utf-8") as f:
        json.dump(config_vars, f)
    monkeypatch.setattr(workflow_routes, "config", config_obj)
    return app


def test_purge_runner_workflows_returns_summary(monkeypatch):
    """/purge-runner-workflows delegates to the kernel purge."""
    from Yuki.server.routes import workflow as workflow_routes
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    summary = {"purged": [], "skipped": [], "dry_run": True}
    with mock.patch.object(workflow_routes, "workflow_purge") as purge:
        purge.purge_stale_workflows.return_value = summary
        r = app.test_client().post(
            "/purge-runner-workflows",
            json={"runner": "pkufarm", "dry_run": True})
    assert r.status_code == 200
    assert r.get_json()["dry_run"] is True
    purge.purge_stale_workflows.assert_called_once_with("r1", True)


def test_purge_runner_workflows_unknown_runner_404(monkeypatch):
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    r = app.test_client().post(
        "/purge-runner-workflows", json={"runner": "nope"})
    assert r.status_code == 404


def test_purge_runner_workflows_missing_runner_400(monkeypatch):
    app = _purge_app(monkeypatch, {
        "runners_id": {"pkufarm": "r1"},
        "backend_types": {"r1": "ssh"},
    })
    r = app.test_client().post(
        "/purge-runner-workflows", json={})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_workflow_purge.py -q`
Expected: 3 FAIL — the route does not exist (Flask returns 404 for the POST, or `workflow_routes` has no `workflow_purge` attribute on the first test).

- [ ] **Step 3: Write minimal implementation**

In `Yuki/server/routes/workflow.py`, extend the imports (currently `os`, `request`, `jsonify`, `ImpressionStorage`, `VWorkflow`, `IN_MOVEMENT`, `translate_to_musical`):

```python
from ...kernel import workflow_purge
from ..config import config
```

Append the route at the end of the file:

```python
@bp.route("/purge-runner-workflows", methods=['POST'])
def purge_runner_workflows():
    """Delete the non-live workflow workspaces on a runner.

    Workflows whose project's synced live set excludes them (and which
    are not running) have their runner-side workspace deleted via the
    per-backend delete_workspace. Live, running, and unknown workflows
    are skipped with reasons. The local Workflows mirror is kept.
    """
    data = request.get_json(silent=True) or request.form
    runner = data.get("runner", "")
    if not runner:
        return jsonify({"error": "missing required field: runner"}), 400
    config_file = config.get_config_file()
    runners_id = config_file.read_variable("runners_id", {})
    if runner not in runners_id:
        return jsonify({"error": f"Runner '{runner}' not found"}), 404
    runner_id = runners_id[runner]
    backend_types = config_file.read_variable("backend_types", {})
    backend_type = backend_types.get(runner_id, "reana")
    if backend_type not in ("ssh", "native", "reana"):
        return jsonify({"error": f"runner '{runner}' has backend "
                                 f"'{backend_type}'"}), 400
    dry_run = str(data.get("dry_run", "")).lower() in ("1", "true", "yes")
    try:
        summary = workflow_purge.purge_stale_workflows(runner_id, dry_run)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return jsonify({"error": str(e)}), 500
    return jsonify(summary)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_workflow_purge.py -q`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add Yuki/server/routes/workflow.py UnitTest/test_workflow_purge.py
git commit -m "feat(server): add /purge-runner-workflows route"
```

---

### Task 6: Yuki verification

**Files:** none.

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest UnitTest/ -q`
Expected: all pass (397 + 12 new = 409).

- [ ] **Step 2: Run pylint on changed files**

Run:
```bash
pylint --disable="fixme,too-many-ancestors,broad-exception-raised,broad-exception-caught,duplicate-code,import-outside-toplevel" \
  Yuki/kernel/liveness.py Yuki/kernel/workflow_purge.py Yuki/kernel/remote_data_ops.py \
  Yuki/server/routes/liveness.py Yuki/server/routes/remote_data.py \
  Yuki/server/routes/workflow.py Yuki/server/app.py \
  UnitTest/test_liveness.py UnitTest/test_workflow_purge.py UnitTest/test_purge_runner_cache.py
```
Expected: no new warnings (pre-existing `vworkflow.py` R0914/R0915 and `status.py` R0914 remain).

- [ ] **Step 3: Commit any fixes** (only if Step 1/2 required changes; otherwise nothing to commit — the tasks above committed already).

---

## CelebiChrono side

Work in the CelebiChrono repo root `/Users/wave/workdir/Celebi/Celebi` (package
sources under `CelebiChrono/`, tests under `UnitTest/` and `tests/`). Test
command there: `python -m pytest UnitTest/ tests/ -q` (run it once at the
start of Task 7 and keep it green).

### Task 7: `CelebiChrono/kernel/liveness.py` — `compute_live_sets`

**Files:**
- Create: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/kernel/liveness.py`
- Test: `/Users/wave/workdir/Celebi/Celebi/UnitTest/test_liveness_compute.py` (new)

**Interfaces:**
- Produces: `compute_live_sets(project_dir) -> (live: list[str], superseded: list[str])` — sorted uuid lists. `live` = current `impression` pointer of every task/algorithm plus transitive input dependencies (`dependencies` + `alias_to_impression` values from `.celebi/impressions/<uuid>/config.json`); `superseded` = history `impressions` uuids not in live.

- [ ] **Step 1: Write the failing tests**

Create `/Users/wave/workdir/Celebi/Celebi/UnitTest/test_liveness_compute.py`:

```python
"""Tests for compute_live_sets (Celebi project-graph liveness)."""
import json
import os

from CelebiChrono.kernel import liveness as celeb_liveness


def _mk_config(path, variables):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(variables, f)


def _mk_object(project, name, object_type, pointer, history):
    obj_dir = os.path.join(project, name)
    _mk_config(os.path.join(obj_dir, "config.json"),
               {"object_type": object_type, "impression": pointer})
    _mk_config(os.path.join(obj_dir, ".celebi", "config.local.json"),
               {"impressions": [{"uuid": u} for u in history]})


def _mk_impression(project, uuid, dependencies=None, aliases=None):
    _mk_config(os.path.join(project, ".celebi", "impressions",
                            uuid, "config.json"),
               {"dependencies": dependencies or [],
                "alias_to_impression": aliases or {}})


def _uuid(seed):
    return (seed * 8).zfill(32)[:32]


def test_compute_live_sets_current_plus_inputs(tmp_path):
    """Live = current pointers plus transitive inputs; history is superseded."""
    project = str(tmp_path / "proj")
    t1 = _uuid("a")
    t2 = _uuid("b")
    old = _uuid("c")
    data = _uuid("d")
    _mk_object(project, "task1", "task", t1, [old])
    _mk_object(project, "algo1", "algorithm", t2, [])
    _mk_impression(project, t1, dependencies=[data])
    _mk_impression(project, t2)
    _mk_impression(project, data)
    _mk_impression(project, old)

    live, superseded = celeb_liveness.compute_live_sets(project)

    assert set(live) == {t1, t2, data}
    assert superseded == [old]


def test_compute_live_sets_ignores_non_objects(tmp_path):
    """Directories without a task/algorithm config contribute nothing."""
    project = str(tmp_path / "proj")
    os.makedirs(os.path.join(project, "readme_dir"))
    _mk_config(os.path.join(project, "readme_dir", "config.json"),
               {"object_type": "directory"})

    live, superseded = celeb_liveness.compute_live_sets(project)

    assert live == []
    assert superseded == []


def test_compute_live_sets_empty_project(tmp_path):
    """An empty project yields empty sets."""
    project = str(tmp_path / "proj")
    os.makedirs(project)
    live, superseded = celeb_liveness.compute_live_sets(project)
    assert live == []
    assert superseded == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from the CelebiChrono repo root): `python -m pytest UnitTest/test_liveness_compute.py -q`
Expected: 3 errors — `ModuleNotFoundError: No module named 'CelebiChrono.kernel.liveness'`

- [ ] **Step 3: Write minimal implementation**

Create `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/kernel/liveness.py`:

```python
"""Compute the project's live impression set: the current version of
every task and algorithm plus its transitive input dependencies."""
import os

from CelebiChrono.utils import metadata
from CelebiChrono.utils.path_utils import project_path as _project_path

IMPRESSIONS_DIR = ".celebi/impressions"


def _object_variables(obj_dir):
    """Merged variable reader across an object's config files.

    Reads <obj>/config.json first, then <obj>/.celebi/config.local.json
    (which records the impression history); the local file wins.
    """
    def read(key, default):
        value = default
        for path in (os.path.join(obj_dir, "config.json"),
                     os.path.join(obj_dir, ".celebi", "config.local.json")):
            if os.path.isfile(path):
                value = metadata.ConfigFile(path).read_variable(key, value)
        return value
    return read


def _objects(project_dir):
    """Yield object_type + variable reader for each task/algorithm dir."""
    if not os.path.isdir(project_dir):
        return
    for name in os.listdir(project_dir):
        obj_dir = os.path.join(project_dir, name)
        if not os.path.isdir(obj_dir) or name.startswith("."):
            continue
        config_path = os.path.join(obj_dir, "config.json")
        if not os.path.isfile(config_path):
            continue
        object_type = metadata.ConfigFile(config_path).read_variable(
            "object_type", "")
        if object_type not in ("task", "algorithm"):
            continue
        yield object_type, _object_variables(obj_dir)


def _impression_config(project_dir, uuid):
    """ConfigFile of an impression in the project's impression store."""
    return metadata.ConfigFile(os.path.join(
        project_dir, IMPRESSIONS_DIR, uuid, "config.json"))


def _collect_inputs(project_dir, root_uuid, seen):
    """Transitively add the impression's dependency uuids to seen."""
    if not root_uuid or root_uuid in seen:
        return
    seen.add(root_uuid)
    config = _impression_config(project_dir, root_uuid)
    for dep in config.read_variable("dependencies", []) or []:
        if isinstance(dep, str):
            _collect_inputs(project_dir, dep, seen)
    aliases = config.read_variable("alias_to_impression", {}) or {}
    for dep in aliases.values():
        if isinstance(dep, str):
            _collect_inputs(project_dir, dep, seen)


def compute_live_sets(project_dir=None):
    """Return (live, superseded) uuid lists for the project.

    live: the current impression pointer of every task/algorithm plus
    its transitive input dependencies.
    superseded: every impression in an object's impression history that
    is not a current pointer (of any object).
    """
    project_dir = project_dir or _project_path()
    live, superseded, current = set(), set(), set()
    for _object_type, read in _objects(project_dir):
        pointer = read("impression", "")
        if pointer:
            current.add(pointer)
            _collect_inputs(project_dir, pointer, live)
        for record in read("impressions", []) or []:
            uuid = record.get("uuid", "") if isinstance(record, dict) else ""
            if uuid:
                superseded.add(uuid)
    live |= current
    superseded -= live
    return sorted(live), sorted(superseded)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_liveness_compute.py -q`
Expected: 3 PASS

- [ ] **Step 5: Commit (in the CelebiChrono repo)**

```bash
git add kernel/liveness.py UnitTest/test_liveness_compute.py
git commit -m "feat(kernel): compute project live/superseded impression sets"
```

---

### Task 8: ChernCommunicator methods

**Files:**
- Modify: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/kernel/chern_communicator.py` (append three methods inside `ChernCommunicator`)
- Test: `/Users/wave/workdir/Celebi/Celebi/UnitTest/test_chern_communicator_liveness.py` (new)

**Interfaces:**
- Produces:
  - `put_live_set(project_uuid, live, superseded) -> dict` — `requests.put` to `http://{serverurl}/live-set/{project_uuid}` with `json={"live": ..., "superseded": ...}`, `timeout=self.timeout`, `raise_for_status()`, returns `r.json()`.
  - `purge_stale_cache(runner, dry_run=False) -> dict` — `requests.post` to `http://{serverurl}/purge-runner-cache` with `json={"runner": runner, "superseded": True, "dry_run": dry_run}`.
  - `purge_stale_workflows(runner, dry_run=False) -> dict` — `requests.post` to `http://{serverurl}/purge-runner-workflows` with `json={"runner": runner, "dry_run": dry_run}`.

- [ ] **Step 1: Write the failing tests**

Create `UnitTest/test_chern_communicator_liveness.py`:

```python
"""Tests for the liveness communicator methods."""
from unittest import mock

from CelebiChrono.kernel.chern_communicator import ChernCommunicator


def _communicator():
    cherncc = ChernCommunicator.instance()
    cherncc.config_file = mock.MagicMock()
    cherncc.config_file.read_variable.return_value = "dite.example:3315"
    cherncc.timeout = 5
    return cherncc


def test_put_live_set_posts_json():
    """put_live_set PUTs the live/superseded lists to DITE."""
    cherncc = _communicator()
    response = mock.MagicMock()
    response.json.return_value = {"stored": True, "live": 1,
                                  "superseded": 0, "live_workflows": 0}
    with mock.patch("CelebiChrono.kernel.chern_communicator.requests") as req:
        req.put.return_value = response
        result = cherncc.put_live_set("proj", ["a"], ["b"])
    req.put.assert_called_once_with(
        "http://dite.example:3315/live-set/proj",
        json={"live": ["a"], "superseded": ["b"]}, timeout=5)
    response.raise_for_status.assert_called_once_with()
    assert result == {"stored": True, "live": 1, "superseded": 0,
                      "live_workflows": 0}


def test_purge_stale_cache_posts_superseded_scope():
    """purge_stale_cache asks for the superseded scope."""
    cherncc = _communicator()
    response = mock.MagicMock()
    response.json.return_value = {"purged": [], "skipped": [],
                                  "dry_run": True}
    with mock.patch("CelebiChrono.kernel.chern_communicator.requests") as req:
        req.post.return_value = response
        result = cherncc.purge_stale_cache("pkufarm", dry_run=True)
    req.post.assert_called_once_with(
        "http://dite.example:3315/purge-runner-cache",
        json={"runner": "pkufarm", "superseded": True, "dry_run": True},
        timeout=5)
    assert result["dry_run"] is True


def test_purge_stale_workflows_posts_runner():
    """purge_stale_workflows posts the runner and dry-run flag."""
    cherncc = _communicator()
    response = mock.MagicMock()
    response.json.return_value = {"purged": [], "skipped": [],
                                  "dry_run": False}
    with mock.patch("CelebiChrono.kernel.chern_communicator.requests") as req:
        req.post.return_value = response
        cherncc.purge_stale_workflows("pkufarm")
    req.post.assert_called_once_with(
        "http://dite.example:3315/purge-runner-workflows",
        json={"runner": "pkufarm", "dry_run": False}, timeout=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest UnitTest/test_chern_communicator_liveness.py -q`
Expected: 3 FAIL — `AttributeError: 'ChernCommunicator' object has no attribute 'put_live_set'`

- [ ] **Step 3: Write minimal implementation**

Append inside the `ChernCommunicator` class (after an existing method, e.g. after `cache_results` if present, or at the end of the class):

```python
    def put_live_set(self, project_uuid, live, superseded):
        """Push the project's live/superseded impression sets to DITE."""
        url = f"http://{self.serverurl()}/live-set/{project_uuid}"
        r = requests.put(url, json={"live": live,
                                    "superseded": superseded},
                         timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def purge_stale_cache(self, runner, dry_run=False):
        """Ask DITE to purge superseded impressions' cache on a runner."""
        url = f"http://{self.serverurl()}/purge-runner-cache"
        r = requests.post(url, json={"runner": runner, "superseded": True,
                                     "dry_run": dry_run},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def purge_stale_workflows(self, runner, dry_run=False):
        """Ask DITE to delete non-live workflow workspaces on a runner."""
        url = f"http://{self.serverurl()}/purge-runner-workflows"
        r = requests.post(url, json={"runner": runner,
                                     "dry_run": dry_run},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_chern_communicator_liveness.py -q`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add kernel/chern_communicator.py UnitTest/test_chern_communicator_liveness.py
git commit -m "feat(communicator): live-set sync and stale-purge calls"
```

---

### Task 9: Shell functions (sync_live, purge_stale_cache, purge_stale_workflows)

**Files:**
- Modify: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/interface/shell_modules/communication.py` (append three functions)
- Modify: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/interface/shell.py` (add the three names to the existing `from .shell_modules.communication import (...)` block)
- Test: `/Users/wave/workdir/Celebi/Celebi/UnitTest/test_shell_liveness.py` (new)

**Interfaces:**
- Produces (shell-visible): `sync_live() -> Message` (best-effort; failures become warning messages, never raise), `purge_stale_cache(runner, dry_run=False) -> Message`, `purge_stale_workflows(runner, dry_run=False) -> Message`.

- [ ] **Step 1: Write the failing tests**

Create `UnitTest/test_shell_liveness.py` (pytest-style, same dir as the other shell tests):

```python
"""Tests for the shell liveness functions."""
import json
import os
from unittest import mock

from CelebiChrono.interface.shell_modules import communication as comm
from CelebiChrono.utils.message import Message


def _project(tmp_path):
    """A minimal project dir with a .celebi/config.json project_uuid."""
    (tmp_path / ".celebi").mkdir()
    with open(tmp_path / ".celebi" / "config.json", "w",
              encoding="utf-8") as f:
        json.dump({"project_uuid": "proj-1"}, f)
    return str(tmp_path)


def test_sync_live_pushes_sets(monkeypatch, tmp_path):
    """sync_live computes the sets and pushes them to DITE."""
    project = _project(tmp_path)
    monkeypatch.setattr("CelebiChrono.utils.path_utils.project_path",
                        lambda: project)
    cherncc = mock.MagicMock()
    cherncc.put_live_set.return_value = {"stored": True, "live": 0,
                                         "superseded": 0,
                                         "live_workflows": 0}
    with mock.patch.object(comm, "ChernCommunicator") as cc:
        cc.instance.return_value = cherncc
        result = comm.sync_live()
    assert isinstance(result, Message)
    cherncc.put_live_set.assert_called_once_with(
        "proj-1", mock.ANY, mock.ANY)


def test_sync_live_failure_is_a_warning(monkeypatch, tmp_path):
    """A failing push returns a Message, never raises."""
    project = _project(tmp_path)
    monkeypatch.setattr("CelebiChrono.utils.path_utils.project_path",
                        lambda: project)
    cherncc = mock.MagicMock()
    cherncc.put_live_set.side_effect = ConnectionError("down")
    with mock.patch.object(comm, "ChernCommunicator") as cc:
        cc.instance.return_value = cherncc
        result = comm.sync_live()  # no raise
    assert isinstance(result, Message)


def test_purge_stale_cache_delegates():
    cherncc = mock.MagicMock()
    cherncc.purge_stale_cache.return_value = {"purged": [], "skipped": [],
                                              "dry_run": True}
    with mock.patch.object(comm, "ChernCommunicator") as cc:
        cc.instance.return_value = cherncc
        result = comm.purge_stale_cache("pkufarm", dry_run=True)
    cherncc.purge_stale_cache.assert_called_once_with("pkufarm", True)
    assert isinstance(result, Message)


def test_purge_stale_workflows_delegates():
    cherncc = mock.MagicMock()
    cherncc.purge_stale_workflows.return_value = {"purged": [],
                                                  "skipped": [],
                                                  "dry_run": False}
    with mock.patch.object(comm, "ChernCommunicator") as cc:
        cc.instance.return_value = cherncc
        result = comm.purge_stale_workflows("pkufarm")
    cherncc.purge_stale_workflows.assert_called_once_with("pkufarm", False)
    assert isinstance(result, Message)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `/Users/wave/workdir/Celebi/Celebi`): `python -m pytest UnitTest/test_shell_liveness.py -q`
Expected: 4 FAIL — `AttributeError: module ... has no attribute 'sync_live'`

- [ ] **Step 3: Write minimal implementation**

Append to `interface/shell_modules/communication.py`:

```python
def sync_live() -> Message:
    """Push the project's live impression set to DITE (best-effort).

    Failures are reported as a warning: a stale set is safe by the
    unknown-is-live rule.
    """
    from CelebiChrono.kernel.liveness import compute_live_sets
    from CelebiChrono.utils import metadata
    from CelebiChrono.utils.path_utils import project_path
    import os
    message = Message()
    try:
        project_dir = project_path()
        project_uuid = metadata.ConfigFile(
            os.path.join(project_dir, ".celebi", "config.json")
        ).read_variable("project_uuid", "")
        if not project_uuid:
            message.add("No project found — run inside a Celebi project.",
                        "error")
            return message
        live, superseded = compute_live_sets(project_dir)
        result = ChernCommunicator.instance().put_live_set(
            project_uuid, live, superseded)
        message.add(f"Synced live set: {result.get('live')} live, "
                    f"{result.get('superseded')} superseded, "
                    f"{result.get('live_workflows')} live workflows")
    except Exception as exc:
        message.add(f"Live-set sync failed (safe to ignore): {exc}",
                    "warning")
    return message


def purge_stale_cache(runner: str, dry_run: bool = False) -> Message:
    """Purge superseded impressions' cache entries on a runner."""
    message = Message()
    try:
        result = ChernCommunicator.instance().purge_stale_cache(
            runner, dry_run=dry_run)
        for entry in result.get("purged", []):
            message.add(f"Purged cache: {entry.get('project')}/"
                        f"{entry.get('impression')}")
        for entry in result.get("skipped", []):
            message.add(f"Skipped cache: {entry.get('project')}/"
                        f"{entry.get('impression')} — {entry.get('reason')}",
                        "warning")
        if result.get("dry_run"):
            message.add(f"Dry run — {len(result.get('purged', []))} cache "
                        "entries would be purged, nothing was deleted.")
        else:
            message.add(f"Purged {len(result.get('purged', []))} cache "
                        f"entries from runner '{runner}'")
    except Exception as exc:
        message.add(f"Purge failed: {exc}", "error")
    return message


def purge_stale_workflows(runner: str, dry_run: bool = False) -> Message:
    """Delete non-live workflow workspaces on a runner."""
    message = Message()
    try:
        result = ChernCommunicator.instance().purge_stale_workflows(
            runner, dry_run=dry_run)
        for entry in result.get("purged", []):
            message.add(f"Purged workflow: {entry.get('project')}/"
                        f"{entry.get('workflow')}")
        for entry in result.get("skipped", []):
            message.add(f"Skipped workflow: {entry.get('project')}/"
                        f"{entry.get('workflow')} — {entry.get('reason')}",
                        "warning")
        if result.get("dry_run"):
            message.add(f"Dry run — {len(result.get('purged', []))} "
                        "workflows would be purged, nothing was deleted.")
        else:
            message.add(f"Purged {len(result.get('purged', []))} workflows "
                        f"from runner '{runner}'")
    except Exception as exc:
        message.add(f"Purge failed: {exc}", "error")
    return message
```

In `interface/shell.py`, add `sync_live`, `purge_stale_cache`, and
`purge_stale_workflows` to the existing
`from .shell_modules.communication import (...)` block (append the names
before the closing parenthesis of that import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_shell_liveness.py -q`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add interface/shell_modules/communication.py interface/shell.py UnitTest/test_shell_liveness.py
git commit -m "feat(shell): sync-live and stale-purge shell functions"
```

---

### Task 10: CLI commands + impress hook

**Files:**
- Create: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/celebi_cli/commands/liveness.py`
- Modify: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/celebi_cli/cli.py` (register three commands)
- Modify: `/Users/wave/workdir/Celebi/Celebi/CelebiChrono/celebi_cli/commands/communication.py` (best-effort sync after impress)
- Test: `/Users/wave/workdir/Celebi/Celebi/tests/test_liveness_commands.py` (new — same dir/style as `test_celebi_cli_log_follow.py`)

**Interfaces:**
- Produces: click commands `sync-live`, `purge-stale-cache <runner> [--dry-run] [--yes]`, `purge-stale-workflows <runner> [--dry-run] [--yes]`; impress triggers a best-effort sync.

- [ ] **Step 1: Write the failing tests**

Create `/Users/wave/workdir/Celebi/Celebi/tests/test_liveness_commands.py` (mirroring `test_celebi_cli_log_follow.py`):

```python
"""Tests for the liveness CLI commands."""
from unittest import mock

from click.testing import CliRunner
from CelebiChrono.celebi_cli.cli import cli


def test_sync_live_command_calls_shell():
    """/sync-live delegates to the shell function."""
    runner = CliRunner()
    with mock.patch("CelebiChrono.interface.shell.sync_live") as fn:
        fn.return_value = "Synced live set: 1 live"
        result = runner.invoke(cli, ["sync-live"])
    assert result.exit_code == 0
    fn.assert_called_once_with()


def test_purge_stale_cache_command_calls_shell():
    """purge-stale-cache passes the runner, honoring dry-run/yes."""
    runner = CliRunner()
    with mock.patch(
            "CelebiChrono.interface.shell.purge_stale_cache") as fn:
        fn.return_value = "Purged 1 cache entries"
        result = runner.invoke(
            cli, ["purge-stale-cache", "pkufarm",
                  "--dry-run", "--yes"])
    assert result.exit_code == 0
    fn.assert_called_once_with("pkufarm", dry_run=True)


def test_purge_stale_workflows_command_calls_shell():
    """purge-stale-workflows passes the runner, honoring --yes."""
    runner = CliRunner()
    with mock.patch(
            "CelebiChrono.interface.shell.purge_stale_workflows") as fn:
        fn.return_value = "Purged 0 workflows"
        result = runner.invoke(
            cli, ["purge-stale-workflows", "pkufarm", "--yes"])
    assert result.exit_code == 0
    fn.assert_called_once_with("pkufarm", dry_run=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `/Users/wave/workdir/Celebi/Celebi`): `python -m pytest tests/test_liveness_commands.py -q`
Expected: 3 FAIL — invoking the unregistered commands yields `UsageError: No such command 'sync-live'`

- [ ] **Step 3: Write minimal implementation**

Create `celebi_cli/commands/liveness.py`:

```python
"""Liveness commands: sync the live set and purge stale runner data."""
import click

from .execution_management import _handle_error, _handle_result


@click.command(name="sync-live")
def sync_live_command() -> None:
    """Push the project's live impression set to DITE."""
    try:
        from CelebiChrono.interface.shell import sync_live
        _handle_result(sync_live())
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")


@click.command(name="purge-stale-cache")
@click.argument("runner", type=str)
@click.option("--dry-run", is_flag=True,
              help="List what would be purged without deleting anything.")
@click.option("--yes", "-y", is_flag=True,
              help="Skip the confirmation prompt.")
def purge_stale_cache_command(runner, dry_run, yes) -> None:
    """Purge superseded impressions' cache entries from a runner.

    RUNNER is the name of the registered runner. Only impressions that
    the project's synced live set marks superseded are selected.
    """
    try:
        from CelebiChrono.interface.shell import purge_stale_cache
        if not dry_run and not yes:
            click.confirm(
                f"Purge superseded impressions' cache on runner "
                f"'{runner}'?", abort=True)
        _handle_result(purge_stale_cache(runner, dry_run=dry_run))
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")


@click.command(name="purge-stale-workflows")
@click.argument("runner", type=str)
@click.option("--dry-run", is_flag=True,
              help="List what would be purged without deleting anything.")
@click.option("--yes", "-y", is_flag=True,
              help="Skip the confirmation prompt.")
def purge_stale_workflows_command(runner, dry_run, yes) -> None:
    """Delete non-live workflow workspaces from a runner.

    RUNNER is the name of the registered runner. Workflows whose
    project's synced live set excludes them (and which are not running)
    are deleted; the local Workflows mirror is always kept.
    """
    try:
        from CelebiChrono.interface.shell import purge_stale_workflows
        if not dry_run and not yes:
            click.confirm(
                f"Purge non-live workflows on runner '{runner}'?",
                abort=True)
        _handle_result(purge_stale_workflows(runner, dry_run=dry_run))
    except ImportError as e:
        _handle_error(f"Failed to import shell function: {e}")
    except Exception as e:
        _handle_error(f"Command failed: {e}")
```

In `celebi_cli/cli.py`, add after the existing `cli.add_command(...)` lines for execution-management commands:

```python
cli.add_command(liveness.sync_live_command)
cli.add_command(liveness.purge_stale_cache_command)
cli.add_command(liveness.purge_stale_workflows_command)
```

and add the import next to the other command-module imports:

```python
from .commands import liveness
```

In `celebi_cli/commands/communication.py` `impress_command`, after `_handle_result(result)`:

```python
        _handle_result(result)
        _best_effort_sync_live()
```

and add the helper at module level (below the imports):

```python
def _best_effort_sync_live() -> None:
    """Push the live set after impress; failures are silently ignored."""
    try:
        from CelebiChrono.interface.shell import sync_live
        sync_live()
    except Exception:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest UnitTest/test_liveness_commands.py -q`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add celebi_cli/commands/liveness.py celebi_cli/cli.py \
        celebi_cli/commands/communication.py UnitTest/test_liveness_commands.py
git commit -m "feat(cli): sync-live, purge-stale-cache, purge-stale-workflows"
```

---

### Task 11: CelebiChrono verification

**Files:** none.

- [ ] **Step 1: Run the full CelebiChrono suite**

Run (from `/Users/wave/workdir/Celebi/Celebi/CelebiChrono`): `python -m pytest UnitTest/ -q`
Expected: all pass (baseline + 13 new).

- [ ] **Step 2: Final cross-check of both repos**

Run the Yuki suite once more (`python -m pytest UnitTest/ -q` from `/Users/wave/workdir/Celebi/Yuki`) to confirm nothing regressed.
Expected: 409 passing.
