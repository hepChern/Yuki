# Environment Re-interpretation for Yuki

## Overview

Extend Yuki's `conda_env_map` configuration to support structured value objects that explicitly declare the target environment type and value. This enables mapping Docker image references to conda environment files (e.g. `docker:rootproject/root_6.32.02` -> `conda:env_root_6.32.02.yaml`) while maintaining full backward compatibility with existing flat-string entries.

## Background

Yuki already has `conda_env_map` in `~/.Yuki/config.json` used by `DryWorkflow._resolve_conda_environment()` to map Docker image strings to conda environment names for local dry-run execution. Currently mapped values are plain strings treated as conda environment names. This design extends the map to support structured objects with `type` and `value` fields.

## Data Model

### New Format

```json
{
  "conda_env_map": {
    "docker:rootproject/root_6.32.02": {
      "type": "conda",
      "value": "env_root_6.32.02.yaml"
    },
    "docker.io/reanahub/reana-env-root6:6.18.04": {
      "type": "conda",
      "value": "reana_env_root6_6_18_04"
    }
  }
}
```

- `type`: `"conda"` (for now; designed to be extensible)
- `value`: The conda environment name or path to a conda env YAML file

### Backward Compatibility

When reading `conda_env_map`, plain string values are transparently wrapped as `{"type": "conda", "value": "<string>"}`. No migration of existing configs is required.

## Resolution Logic

`DryWorkflow._resolve_conda_environment()` is updated to use the new `EnvInterpreter` utility:

1. Read `conda_env_map` from `~/.Yuki/config.json`
2. Look up the raw environment string
3. If found:
   - If the value is a dict with `type == "conda"`, return `value`
   - If the value is a plain string, return it (backward compat)
4. If not found, apply existing default sanitization:
   - Strip prefixes `docker://`, `docker.io/`, `docker:`
   - Replace `/` and `:` with `_`

## CLI Commands

New `env-map` group added to the `yuki` CLI:

```bash
# Add a mapping
yuki env-map add <source_env> <type> <value>
yuki env-map add "docker:rootproject/root_6.32.02" conda "env_root_6.32.02.yaml"

# List all mappings
yuki env-map list

# Remove a mapping
yuki env-map remove <source_env>
yuki env-map remove "docker:rootproject/root_6.32.02"
```

The CLI reads from and writes to `~/.Yuki/config.json` directly.

## Components

### `Yuki/utils/env_interpreter.py` (new)

`EnvInterpreter` class:
- `normalize_entry(entry)` — convert plain string to `{"type": "conda", "value": ...}` dict
- `resolve(environment, config_path)` — look up environment in `conda_env_map`, return resolved value or None
- `read_map(config_path)` — read and normalize the full map
- `add_mapping(config_path, source, env_type, value)` — add/update a mapping
- `remove_mapping(config_path, source)` — remove a mapping
- `list_mappings(config_path)` — return all mappings

### `Yuki/kernel/dry_workflow.py` (modified)

Update `_resolve_conda_environment()` to delegate to `EnvInterpreter.resolve()`.

### `Yuki/main.py` (modified)

Add `env-map` Click group with `add`, `list`, `remove` subcommands.

### `UnitTest/` (new tests)

- Tests for `EnvInterpreter` normalization, resolution, add/remove/list
- Tests for CLI commands

## Files to Modify

| File | Change |
|------|--------|
| `Yuki/utils/env_interpreter.py` | **New** — `EnvInterpreter` class |
| `Yuki/kernel/dry_workflow.py` | Update `_resolve_conda_environment()` |
| `Yuki/main.py` | Add `env-map` CLI commands |
| `UnitTest/test_env_interpreter.py` | **New** — Unit tests |
| `UnitTest/test_cli_env_map.py` | **New** — CLI tests |
