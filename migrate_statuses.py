#!/usr/bin/env python3
"""
Migration script for Yuki job status system enhancement.

This script migrates existing job status.json files from legacy status names
to musical status names, adding detailed_status and status_legacy fields.
"""

import os
import json
import argparse
from pathlib import Path

# Import the translation functions
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Yuki.kernel.status_constants import (
    translate_to_musical, translate_to_legacy, get_detailed_status_message,
    LEGACY_TO_MUSICAL, MUSICAL_TO_LEGACY,
    SILENCE, PRELUDE, IN_MOVEMENT, COMPOSING, ORCHESTRATING,
    TUNING, DISSONANCE, CODA, FINAL_NOTE,
    FAILED, STOPPED, DELETED, ARCHIVED
)


def migrate_status_file(status_file_path, dry_run=True, backup=True):
    """Migrate a single status.json file."""
    try:
        with open(status_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  Error reading {status_file_path}: {e}")
        return False

    # Check if already migrated (has status_legacy field)
    if "status_legacy" in data:
        print(f"  Already migrated: {status_file_path}")
        return True

    old_status = data.get("status", "raw")

    # Skip if already a musical status
    if old_status in [SILENCE, PRELUDE, IN_MOVEMENT, COMPOSING, ORCHESTRATING,
                      TUNING, DISSONANCE, CODA, FINAL_NOTE,
                      FAILED, STOPPED, DELETED, ARCHIVED]:
        print(f"  Already musical status: {status_file_path} ({old_status})")
        return True

    # Translate to musical name
    new_status = translate_to_musical(old_status)

    # Generate detailed status message
    detailed_status = data.get("detailed_status", "")
    if not detailed_status:
        detailed_status = get_detailed_status_message(new_status)

    # Create backup if requested
    if backup and not dry_run:
        backup_path = status_file_path + ".backup"
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"  Created backup: {backup_path}")

    # Update data
    data["status"] = new_status
    data["status_legacy"] = old_status
    data["detailed_status"] = detailed_status

    if dry_run:
        print(f"  Would migrate: {status_file_path}")
        print(f"    {old_status} -> {new_status}")
        print(f"    detailed_status: {detailed_status}")
        return True

    # Write updated data
    try:
        with open(status_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"  Migrated: {status_file_path}")
        print(f"    {old_status} -> {new_status}")
        return True
    except Exception as e:
        print(f"  Error writing {status_file_path}: {e}")
        return False


def find_status_files(storage_root=None):
    """Find all status.json files in the Yuki storage hierarchy."""
    if storage_root is None:
        storage_root = os.path.join(os.path.expanduser("~"), ".Yuki", "Storage")

    status_files = []

    for project_dir in Path(storage_root).glob("*"):
        if not project_dir.is_dir():
            continue

        for impression_dir in project_dir.glob("*"):
            if not impression_dir.is_dir():
                continue

            status_file = impression_dir / "status.json"
            if status_file.exists():
                status_files.append(str(status_file))

    return status_files


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Yuki job statuses from legacy to musical names"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be migrated without making changes"
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Don't create backup files"
    )
    parser.add_argument(
        "--storage-root",
        default=os.path.join(os.path.expanduser("~"), ".Yuki", "Storage"),
        help="Path to Yuki storage root directory"
    )
    parser.add_argument(
        "--file", action="append",
        help="Specific status.json file to migrate (can be used multiple times)"
    )

    args = parser.parse_args()

    print("Yuki Status Migration Tool")
    print("=" * 50)
    print(f"Dry run: {args.dry_run}")
    print(f"Backup: {not args.no_backup}")
    print(f"Storage root: {args.storage_root}")
    print()

    # Get list of files to migrate
    if args.file:
        status_files = args.file
    else:
        print("Finding status.json files...")
        status_files = find_status_files(args.storage_root)

    print(f"Found {len(status_files)} status.json files")
    print()

    # Migration statistics
    migrated = 0
    failed = 0
    skipped = 0

    for i, status_file in enumerate(status_files, 1):
        print(f"[{i}/{len(status_files)}] Processing: {status_file}")

        if not os.path.exists(status_file):
            print(f"  File not found: {status_file}")
            failed += 1
            continue

        success = migrate_status_file(
            status_file,
            dry_run=args.dry_run,
            backup=not args.no_backup
        )

        if success:
            migrated += 1
        else:
            failed += 1

    print()
    print("Migration Summary")
    print("=" * 50)
    print(f"Total files: {len(status_files)}")
    print(f"Migrated: {migrated}")
    print(f"Failed: {failed}")

    if args.dry_run:
        print("\nThis was a dry run. No files were modified.")
        print("Run without --dry-run to perform the migration.")
    else:
        print("\nMigration completed.")

    # Show translation table
    print()
    print("Status Translation Table")
    print("=" * 50)
    print("Legacy -> Musical")
    for legacy, musical in LEGACY_TO_MUSICAL.items():
        print(f"  {legacy:20} -> {musical}")
    print("\nMusical -> Legacy")
    for musical, legacy in MUSICAL_TO_LEGACY.items():
        if musical != legacy:  # Skip identities
            print(f"  {musical:20} -> {legacy}")


if __name__ == "__main__":
    main()