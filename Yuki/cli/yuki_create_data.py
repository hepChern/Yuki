#!/usr/bin/env python3
"""Standalone CLI to create a rawdata impression directly in Yuki storage.

This bypasses the HTTP upload for very large datasets by copying data
locally into the Yuki impression directory and creating matching metadata.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from CelebiChrono.kernel.vimpression import VImpression
from CelebiChrono.utils import csys
from CelebiChrono.utils import metadata


def create_canonical_rawdata_task(temp_dir: str, descriptor: str, data_md5: str) -> None:
    """Create a canonical rawdata task directory for UUID generation."""
    csys.mkdir(os.path.join(temp_dir, ".celebi"))
    config = metadata.ConfigFile(os.path.join(temp_dir, ".celebi", "config.json"))
    config.write_variable("object_type", "task")
    readme_path = os.path.join(temp_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(f"Please write README for task {descriptor}")
    yaml_file = metadata.YamlFile(os.path.join(temp_dir, "celebi.yaml"))
    yaml_file.write_variable("environment", "rawdata")
    yaml_file.write_variable("uuid", data_md5)
    yaml_file.write_variable("descriptor", descriptor)


def fast_copy_tree(src: str, dst: str) -> None:
    """Copy a directory tree using the fastest available mechanism."""
    # 1. Try reflink (copy-on-write) via cp --reflink=auto
    if shutil.which("cp"):
        result = subprocess.run(
            ["cp", "-a", "--reflink=auto", src + "/", dst + "/"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
    # 2. Try hard links for same-filesystem instant copy
    try:
        shutil.copytree(src, dst, copy_function=os.link)
        return
    except OSError:
        pass
    # 3. Try rsync for large cross-filesystem copies
    if shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-a", "--progress", src + "/", dst + "/"],
            check=True,
        )
        return
    # 4. Fallback to standard Python copy
    shutil.copytree(src, dst, copy_function=shutil.copy)


def build_impression_config(project_uuid: str, impression_uuid: str, temp_dir: str) -> dict:
    """Build a minimal impression config.json matching Celebi format."""
    file_list = csys.tree_excluded(temp_dir)
    return {
        "object_type": "task",
        "tree": file_list,
        "dependencies": [],
        "current_path": "",
        "alias_to_impression": {},
        "parents": [],
        "storage_backend": "",
        "root_tree": "",
        "project_uuid": project_uuid,
        "impression_uuid": impression_uuid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a rawdata impression in Yuki storage without HTTP upload."
    )
    parser.add_argument("--yuki-dir", required=True, help="Yuki storage root directory")
    parser.add_argument("--project-uuid", required=True, help="Project UUID")
    parser.add_argument("--data-dir", required=True, help="Source data directory")
    parser.add_argument(
        "--descriptor",
        default=None,
        help="Task descriptor (defaults to basename of data-dir)",
    )
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        print(f"Error: data-dir does not exist: {data_dir}", file=sys.stderr)
        return 1

    descriptor = args.descriptor or os.path.basename(os.path.normpath(data_dir))

    print("Computing MD5 of data directory...")
    data_md5 = csys.dir_md5(data_dir)
    print(f"Data MD5: {data_md5}")

    print("Building canonical rawdata task to compute impression UUID...")
    with tempfile.TemporaryDirectory(prefix="yuki_canonical_") as temp_dir:
        create_canonical_rawdata_task(temp_dir, descriptor, data_md5)
        impression_uuid = VImpression().generate_imp_uuid(
            args.project_uuid, temp_dir, []
        )

    print(f"Impression UUID: {impression_uuid}")

    storage_dir = os.path.join(args.yuki_dir, "Storage")
    impression_dir = os.path.join(
        storage_dir, args.project_uuid, impression_uuid
    )
    rawdata_dir = os.path.join(impression_dir, "rawdata")
    contents_dir = os.path.join(impression_dir, "contents")
    status_path = os.path.join(impression_dir, "status.json")
    config_path = os.path.join(impression_dir, "config.json")

    if os.path.exists(impression_dir):
        print(f"Warning: impression directory already exists: {impression_dir}")
    else:
        os.makedirs(impression_dir, exist_ok=True)

    print("Copying task metadata to contents/ ...")
    with tempfile.TemporaryDirectory(prefix="yuki_canonical_") as temp_dir:
        create_canonical_rawdata_task(temp_dir, descriptor, data_md5)
        if os.path.exists(contents_dir):
            shutil.rmtree(contents_dir)
        shutil.copytree(temp_dir, contents_dir)
        impression_config = build_impression_config(
            args.project_uuid, impression_uuid, temp_dir
        )

    print("Writing config.json ...")
    config_file = metadata.ConfigFile(config_path)
    for key, value in impression_config.items():
        config_file.write_variable(key, value)

    print("Writing status.json (pending) ...")
    status_file = metadata.ConfigFile(status_path)
    status_file.write_variable("status", "pending")

    print(f"Copying data to {rawdata_dir} ...")
    if os.path.exists(rawdata_dir):
        shutil.rmtree(rawdata_dir)
    fast_copy_tree(data_dir, rawdata_dir)

    manifest = {
        "uuid": impression_uuid,
        "md5": data_md5,
        "descriptor": descriptor,
        "project_uuid": args.project_uuid,
        "yuki_dir": args.yuki_dir,
        "data_dir": data_dir,
    }
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
