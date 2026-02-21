"""
Bulk impression stageout export/import routes.
"""
import io
import os
import re
import tarfile
from logging import getLogger

from flask import Blueprint, request, send_file, jsonify

from ..config import config

bp = Blueprint('transfer', __name__)
logger = getLogger("YukiLogger")

UUID_RE = re.compile(r'^[0-9a-f]{32}$')


@bp.route('/export-imp-stageout', methods=['POST'])
def export_impressions():
    """Export stageout results from multiple impressions as a tar.gz."""
    data = request.get_json()
    project_uuid = data.get("project_uuid")
    impressions = data.get("impressions", [])

    if not project_uuid or not impressions:
        return jsonify({"error": "project_uuid and impressions are required"}), 400

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for impression in impressions:
            job_path = config.get_job_path(project_uuid, impression)
            if not os.path.isdir(job_path):
                continue
            for entry in os.listdir(job_path):
                stageout_dir = os.path.join(job_path, entry, "stageout")
                if not os.path.isdir(stageout_dir):
                    continue
                runner_id = entry
                for root, dirs, files in os.walk(stageout_dir):
                    for fname in files:
                        full_path = os.path.join(root, fname)
                        rel = os.path.relpath(full_path, stageout_dir)
                        arcname = os.path.join(impression, runner_id, "stageout", rel)
                        tar.add(full_path, arcname=arcname)

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/gzip",
        as_attachment=True,
        download_name="impressions_export.tar.gz",
    )


@bp.route('/import-imp-stageout', methods=['POST'])
def import_impressions():
    """Import stageout results from a tar into the corresponding job folders."""
    project_uuid = request.form.get("project_uuid")
    if not project_uuid:
        return jsonify({"error": "project_uuid is required"}), 400

    tar_file = request.files.get("tarfile")
    if not tar_file:
        return jsonify({"error": "tarfile is required"}), 400

    imported = set()
    with tarfile.open(fileobj=tar_file.stream, mode="r:*") as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue
            parts = member.name.split("/")
            # Expected: {impression}/{runner_id}/stageout/{...files}
            if len(parts) < 4 or parts[2] != "stageout":
                continue
            if ".." in parts or any(p.startswith("/") for p in parts):
                continue
            impression = parts[0]
            if not UUID_RE.match(impression):
                continue

            target_dir = os.path.join(
                config.storage_path, project_uuid, *parts[:-1]
            )
            os.makedirs(target_dir, exist_ok=True)

            source = tar.extractfile(member)
            if source is None:
                continue
            target_path = os.path.join(config.storage_path, project_uuid, member.name)
            with open(target_path, "wb") as f:
                f.write(source.read())

            imported.add(impression)

    return jsonify({"imported": sorted(imported), "count": len(imported)})
