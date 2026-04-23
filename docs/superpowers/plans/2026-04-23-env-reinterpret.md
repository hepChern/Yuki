# Environment Re-interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Yuki's `conda_env_map` to support structured value objects (`{"type": "conda", "value": "..."}`) and add CLI commands to manage mappings.

**Architecture:** A new `EnvInterpreter` utility normalizes map entries and resolves environment strings. `DryWorkflow` delegates to it. CLI commands read/write `~/.Yuki/config.json` directly.

**Tech Stack:** Python, Click CLI, unittest, json

---

## File Structure

| File | Responsibility |
|------|----------------|
| `Yuki/utils/env_interpreter.py` | **New.** Core logic: normalize entries, resolve env strings, CRUD operations on `conda_env_map`. |
| `Yuki/kernel/dry_workflow.py` | **Modify.** Update `_resolve_conda_environment()` to use `EnvInterpreter`. |
| `Yuki/main.py` | **Modify.** Add `env-map` Click group with `add`, `list`, `remove` subcommands. |
| `UnitTest/test_env_interpreter.py` | **New.** Tests for `EnvInterpreter` class. |

---

## Task 1: EnvInterpreter Utility

**Files:**
- Create: `Yuki/utils/env_interpreter.py`
- Test: `UnitTest/test_env_interpreter.py`

- [ ] **Step 1: Write the failing test**

Create `UnitTest/test_env_interpreter.py`:

```python
"""Tests for EnvInterpreter utility."""
import os
import json
import tempfile
import unittest

from Yuki.utils.env_interpreter import EnvInterpreter


class TestEnvInterpreter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.remove(self.config_path)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    # --- normalize_entry ---
    def test_normalize_entry_plain_string(self):
        result = EnvInterpreter.normalize_entry("my_env")
        self.assertEqual(result, {"type": "conda", "value": "my_env"})

    def test_normalize_entry_dict(self):
        entry = {"type": "conda", "value": "env.yaml"}
        result = EnvInterpreter.normalize_entry(entry)
        self.assertEqual(result, entry)

    def test_normalize_entry_invalid_type_raises(self):
        with self.assertRaises(TypeError):
            EnvInterpreter.normalize_entry(123)

    # --- resolve ---
    def test_resolve_mapped_dict(self):
        self._write_config({
            "conda_env_map": {
                "docker:img": {"type": "conda", "value": "env.yaml"}
            }
        })
        result = EnvInterpreter.resolve("docker:img", self.config_path)
        self.assertEqual(result, "env.yaml")

    def test_resolve_mapped_plain_string(self):
        self._write_config({
            "conda_env_map": {
                "docker:img": "my_env"
            }
        })
        result = EnvInterpreter.resolve("docker:img", self.config_path)
        self.assertEqual(result, "my_env")

    def test_resolve_not_found(self):
        self._write_config({"conda_env_map": {}})
        result = EnvInterpreter.resolve("docker:missing", self.config_path)
        self.assertIsNone(result)

    def test_resolve_missing_config_file(self):
        result = EnvInterpreter.resolve("docker:img", self.config_path)
        self.assertIsNone(result)

    def test_resolve_missing_conda_env_map(self):
        self._write_config({})
        result = EnvInterpreter.resolve("docker:img", self.config_path)
        self.assertIsNone(result)

    # --- add_mapping ---
    def test_add_mapping_new(self):
        self._write_config({"conda_env_map": {}})
        EnvInterpreter.add_mapping(
            self.config_path, "docker:img", "conda", "env.yaml"
        )
        with open(self.config_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(
            data["conda_env_map"]["docker:img"],
            {"type": "conda", "value": "env.yaml"}
        )

    def test_add_mapping_overwrite(self):
        self._write_config({
            "conda_env_map": {"docker:img": "old_env"}
        })
        EnvInterpreter.add_mapping(
            self.config_path, "docker:img", "conda", "new_env"
        )
        with open(self.config_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(
            data["conda_env_map"]["docker:img"],
            {"type": "conda", "value": "new_env"}
        )

    def test_add_mapping_creates_conda_env_map(self):
        self._write_config({})
        EnvInterpreter.add_mapping(
            self.config_path, "docker:img", "conda", "env.yaml"
        )
        with open(self.config_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("conda_env_map", data)

    # --- remove_mapping ---
    def test_remove_mapping(self):
        self._write_config({
            "conda_env_map": {
                "docker:img": {"type": "conda", "value": "env.yaml"}
            }
        })
        EnvInterpreter.remove_mapping(self.config_path, "docker:img")
        with open(self.config_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("docker:img", data["conda_env_map"])

    def test_remove_mapping_not_found(self):
        self._write_config({"conda_env_map": {}})
        EnvInterpreter.remove_mapping(self.config_path, "docker:missing")
        # should not raise

    # --- list_mappings ---
    def test_list_mappings(self):
        self._write_config({
            "conda_env_map": {
                "docker:a": {"type": "conda", "value": "env_a.yaml"},
                "docker:b": "env_b"
            }
        })
        result = EnvInterpreter.list_mappings(self.config_path)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["docker:a"], {"type": "conda", "value": "env_a.yaml"})
        self.assertEqual(result["docker:b"], {"type": "conda", "value": "env_b"})

    def test_list_mappings_empty(self):
        self._write_config({})
        result = EnvInterpreter.list_mappings(self.config_path)
        self.assertEqual(result, {})

    def test_list_mappings_missing_file(self):
        result = EnvInterpreter.list_mappings(self.config_path)
        self.assertEqual(result, {})
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/wave/workdir/Celebi/Yuki
python -m pytest UnitTest/test_env_interpreter.py -v
```

Expected: ImportError or AttributeError for `EnvInterpreter` not found.

- [ ] **Step 3: Write minimal implementation**

Create `Yuki/utils/env_interpreter.py`:

```python
"""Environment interpreter utility for resolving environment mappings."""
import os
from typing import Any, Dict, Optional

from CelebiChrono.utils import metadata


class EnvInterpreter:
    """Utility to read, write, and resolve conda_env_map entries.

    Supports both legacy plain-string values and structured dict values
    with ``type`` and ``value`` keys.
    """

    @staticmethod
    def normalize_entry(entry: Any) -> Dict[str, str]:
        """Normalize a conda_env_map entry to a dict.

        Args:
            entry: Either a plain string or a dict with ``type`` and ``value``.

        Returns:
            A dict in the form ``{"type": "conda", "value": "..."}``.

        Raises:
            TypeError: If ``entry`` is neither a string nor a dict.
        """
        if isinstance(entry, str):
            return {"type": "conda", "value": entry}
        if isinstance(entry, dict):
            return entry
        raise TypeError(f"Invalid conda_env_map entry type: {type(entry).__name__}")

    @staticmethod
    def resolve(environment: str, config_path: str) -> Optional[str]:
        """Resolve an environment string using conda_env_map.

        Args:
            environment: The raw environment string (e.g. ``docker:img``).
            config_path: Path to the JSON config file.

        Returns:
            The resolved value (e.g. ``env.yaml``) or ``None`` if not mapped.
        """
        if not os.path.exists(config_path):
            return None

        config = metadata.ConfigFile(config_path)
        env_map = config.read_variable("conda_env_map", {})
        if environment in env_map:
            entry = EnvInterpreter.normalize_entry(env_map[environment])
            return entry["value"]
        return None

    @staticmethod
    def add_mapping(
        config_path: str, source: str, env_type: str, value: str
    ) -> None:
        """Add or overwrite a mapping in conda_env_map.

        Args:
            config_path: Path to the JSON config file.
            source: The source environment string.
            env_type: The target environment type (e.g. ``conda``).
            value: The target environment value.
        """
        config = metadata.ConfigFile(config_path)
        env_map = config.read_variable("conda_env_map", {})
        env_map[source] = {"type": env_type, "value": value}
        config.write_variable("conda_env_map", env_map)

    @staticmethod
    def remove_mapping(config_path: str, source: str) -> None:
        """Remove a mapping from conda_env_map.

        Args:
            config_path: Path to the JSON config file.
            source: The source environment string to remove.
        """
        config = metadata.ConfigFile(config_path)
        env_map = config.read_variable("conda_env_map", {})
        if source in env_map:
            del env_map[source]
            config.write_variable("conda_env_map", env_map)

    @staticmethod
    def list_mappings(config_path: str) -> Dict[str, Dict[str, str]]:
        """List all normalized mappings from conda_env_map.

        Args:
            config_path: Path to the JSON config file.

        Returns:
            Dict of source -> normalized entry dict.
        """
        if not os.path.exists(config_path):
            return {}

        config = metadata.ConfigFile(config_path)
        env_map = config.read_variable("conda_env_map", {})
        return {
            source: EnvInterpreter.normalize_entry(entry)
            for source, entry in env_map.items()
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/wave/workdir/Celebi/Yuki
python -m pytest UnitTest/test_env_interpreter.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/wave/workdir/Celebi/Yuki
git add Yuki/utils/env_interpreter.py UnitTest/test_env_interpreter.py
git commit -m "feat: add EnvInterpreter utility for environment re-interpretation

- Supports structured dict entries with type/value
- Maintains backward compatibility with plain string entries
- Provides CRUD operations on conda_env_map"
```

---

## Task 2: Update DryWorkflow

**Files:**
- Modify: `Yuki/kernel/dry_workflow.py:176-202`

- [ ] **Step 1: Modify `_resolve_conda_environment`**

In `Yuki/kernel/dry_workflow.py`, add the import and update the method:

Add import at the top of the file (after existing imports):
```python
from Yuki.utils.env_interpreter import EnvInterpreter
```

Replace `_resolve_conda_environment` (lines 176-202) with:

```python
    def _resolve_conda_environment(self, environment):
        """Map a job environment string to a conda environment name.

        Resolution order:
        1. ``conda_env_map`` from ~/.Yuki/config.json (structured or plain)
        2. Strip common Docker prefixes and sanitise the image name
        """
        if not environment:
            environment = "docker.io/reanahub/reana-env-root6:6.18.04"

        config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
        resolved = EnvInterpreter.resolve(environment, config_path)
        if resolved is not None:
            return resolved

        env_name = environment
        for prefix in ("docker://", "docker.io/", "docker:"):
            if env_name.startswith(prefix):
                env_name = env_name[len(prefix):]

        return env_name.replace("/", "_").replace(":", "_")
```

Note the addition of `"docker:"` to the prefix stripping list.

- [ ] **Step 2: Verify no regressions**

Run existing Yuki tests (if any):
```bash
cd /Users/wave/workdir/Celebi/Yuki
python -m pytest UnitTest/ -v
```

If there are no meaningful tests, at least verify the module imports cleanly:
```bash
cd /Users/wave/workdir/Celebi/Yuki
python -c "from Yuki.kernel.dry_workflow import DryWorkflow; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/wave/workdir/Celebi/Yuki
git add Yuki/kernel/dry_workflow.py
git commit -m "feat(dry_workflow): use EnvInterpreter for conda env resolution

- Delegates conda_env_map lookup to EnvInterpreter
- Maintains backward compatibility with plain string entries
- Adds 'docker:' prefix stripping support"
```

---

## Task 3: Add CLI Commands

**Files:**
- Modify: `Yuki/main.py`

- [ ] **Step 1: Add the env-map CLI group**

Add the following code to `Yuki/main.py` before the `main()` function (after the `run_workflow` command, around line 91):

```python
# ------ Environment Map ------ #
@cli.group()
def env_map():
    """Manage conda_env_map environment re-interpretations."""


@env_map.command('add')
@click.argument('source')
@click.argument('env_type')
@click.argument('value')
def env_map_add(source, env_type, value):
    """Add or update an environment mapping.

    SOURCE is the original environment string (e.g. docker:img).
    TYPE is the target type (e.g. conda).
    VALUE is the target environment value (e.g. env.yaml).
    """
    config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
    from Yuki.utils.env_interpreter import EnvInterpreter
    EnvInterpreter.add_mapping(config_path, source, env_type, value)
    click.echo(f"Mapped '{source}' -> {env_type}:{value}")


@env_map.command('list')
def env_map_list():
    """List all environment mappings."""
    config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
    from Yuki.utils.env_interpreter import EnvInterpreter
    mappings = EnvInterpreter.list_mappings(config_path)
    if not mappings:
        click.echo("No environment mappings configured.")
        return
    for source, entry in mappings.items():
        click.echo(f"{source} -> {entry['type']}:{entry['value']}")


@env_map.command('remove')
@click.argument('source')
def env_map_remove(source):
    """Remove an environment mapping.

    SOURCE is the original environment string to unmap.
    """
    config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
    from Yuki.utils.env_interpreter import EnvInterpreter
    EnvInterpreter.remove_mapping(config_path, source)
    click.echo(f"Removed mapping for '{source}'.")
```

- [ ] **Step 2: Verify CLI commands work**

Install Yuki in development mode (if not already):
```bash
cd /Users/wave/workdir/Celebi/Yuki
pip install -e .
```

Test the commands:
```bash
# List (empty)
yuki env-map list
# Expected: "No environment mappings configured."

# Add
yuki env-map add "docker:rootproject/root_6.32.02" conda "env_root_6.32.02.yaml"
# Expected: "Mapped 'docker:rootproject/root_6.32.02' -> conda:env_root_6.32.02.yaml"

# List again
yuki env-map list
# Expected: "docker:rootproject/root_6.32.02 -> conda:env_root_6.32.02.yaml"

# Remove
yuki env-map remove "docker:rootproject/root_6.32.02"
# Expected: "Removed mapping for 'docker:rootproject/root_6.32.02'."

# List (empty again)
yuki env-map list
# Expected: "No environment mappings configured."
```

- [ ] **Step 3: Commit**

```bash
cd /Users/wave/workdir/Celebi/Yuki
git add Yuki/main.py
git commit -m "feat(cli): add env-map commands for managing environment mappings

- add: create or update a mapping
- list: show all normalized mappings
- remove: delete a mapping"
```

---

## Task 4: Integration Verification

- [ ] **Step 1: Test end-to-end flow**

```bash
# Add a mapping
yuki env-map add "docker:test/image:v1" conda "env_test.yaml"

# Verify it appears in config
cat ~/.Yuki/config.json | python -m json.tool
# Expected: conda_env_map contains {"docker:test/image:v1": {"type": "conda", "value": "env_test.yaml"}}

# Verify DryWorkflow can resolve it
cd /Users/wave/workdir/Celebi/Yuki
python -c "
from Yuki.utils.env_interpreter import EnvInterpreter
import os
config = os.path.join(os.environ['HOME'], '.Yuki', 'config.json')
print(EnvInterpreter.resolve('docker:test/image:v1', config))
"
# Expected: env_test.yaml
```

- [ ] **Step 2: Test backward compatibility**

Create a config with a legacy plain-string entry and verify resolution:
```bash
python -c "
import json, os
config = os.path.join(os.environ['HOME'], '.Yuki', 'config.json')
with open(config, 'r') as f:
    data = json.load(f)
data['conda_env_map']['legacy:docker'] = 'legacy_env'
with open(config, 'w') as f:
    json.dump(data, f)
"

python -c "
from Yuki.utils.env_interpreter import EnvInterpreter
import os
config = os.path.join(os.environ['HOME'], '.Yuki', 'config.json')
print(EnvInterpreter.resolve('legacy:docker', config))
"
# Expected: legacy_env
```

- [ ] **Step 3: Commit**

```bash
cd /Users/wave/workdir/Celebi/Yuki
git commit --allow-empty -m "test: verify env-map CLI and DryWorkflow integration"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Structured value objects in `conda_env_map` — Task 1
- [x] Backward compatibility with plain strings — Task 1
- [x] Resolution logic in DryWorkflow — Task 2
- [x] CLI commands: add, list, remove — Task 3
- [x] Tests — Task 1

**Placeholder scan:**
- [x] No TBD, TODO, or "implement later"
- [x] All code blocks contain complete code
- [x] All commands have expected output

**Type consistency:**
- [x] `EnvInterpreter.normalize_entry` returns `Dict[str, str]` consistently
- [x] `EnvInterpreter.resolve` returns `Optional[str]` consistently
- [x] CLI arg names match between command definitions and help text
