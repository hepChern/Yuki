"""
File upload and download routes.
"""
import os
import tarfile
from logging import getLogger

from flask import Blueprint, request, send_from_directory

from ..config import config

bp = Blueprint('upload', __name__)
logger = getLogger("YukiLogger")


@bp.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """Handle file uploads."""
    if request.method == 'POST':
        print("Trying to upload files:", request.form)

        tarname = request.form["tarname"]
        request.files[tarname].save(os.path.join("/tmp", tarname))

        with tarfile.open(os.path.join("/tmp", tarname), "r") as tar:
            for ti in tar:
                tar.extract(ti, os.path.join(config.storage_path, tarname[:-7]))

        config_file = request.form['config']
        logger.info(config_file)
        request.files[config_file].save(
            os.path.join(config.storage_path, tarname[:-7], config_file)
        )
    return "Successful"


@bp.route("/download/<filename>", methods=['GET'])
def download_file(filename):
    """Download a file."""
    directory = os.path.join(os.getcwd(), "data")
    return send_from_directory(directory, filename, as_attachment=True)


@bp.route("/export/<impression>/<filename>", methods=['GET'])
def export(impression, filename):
    """Export a file from an impression."""
    job_path = config.get_job_path(impression)
    config_file = config.get_config_file()
    runners = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    # Search for the first machine that has the file
    for runner in runners:
        runner_id = runners_id[runner]
        path = os.path.join(job_path, runner_id, "outputs")
        full_path = os.path.join(path, filename)
        print("path", full_path)
        if os.path.exists(full_path):
            return send_from_directory(path, filename, as_attachment=True)
    return "NOTFOUND"


@bp.route("/getfile/<impression>/<filename>", methods=['GET'])
def get_file(impression, filename):
    """Get the path to a specific file in an impression."""
    job_path = config.get_job_path(impression)
    config_file = config.get_config_file()
    runners = config_file.read_variable("runners", [])
    runners_id = config_file.read_variable("runners_id", {})

    for machine in runners:
        machine_id = runners_id[machine]
        path = os.path.join(job_path, machine_id, "outputs", filename)
        if os.path.exists(path):
            return path
    return "NOTFOUND"


@bp.route("/fileview/<impression>/<runner_id>/<filename>", methods=['GET'])
def fileview(impression, runner_id, filename):
    """View a specific file."""
    job_path = config.get_job_path(impression)
    path = os.path.join(job_path, runner_id, "outputs")
    print(path)
    return send_from_directory(path, filename)
