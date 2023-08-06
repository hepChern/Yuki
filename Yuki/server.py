import daemon
import tarfile
from daemon import pidfile
import subprocess
from flask import Flask
from flask import request
from flask import render_template
from flask import send_from_directory
import os

from Yuki.kernel.VJob import VJob
from Yuki.kernel.VImage import VImage
from Yuki.kernel.VContainer import VContainer
from Yuki.kernel.VWorkflow import VWorkflow
from Chern.utils.metadata import ConfigFile
from Chern.utils.pretty import colorize
import sys
from celery import Celery
from logging import getLogger
import logging

from multiprocessing import Process

app = Flask(__name__)

# logging.basicConfig(level=logging.DEBUG)
logger = getLogger("YukiLogger")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('[%(asctime)s][%(levelname)s] - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

app.config['SECRET_KEY'] = 'top-secret!'

# Celery configuration
app.config['CELERY_BROKER_URL'] = 'amqp://localhost'
app.config['CELERY_RESULT_BACKEND'] = 'rpc://'

celeryapp = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celeryapp.conf.update(app.config)

import sqlite3

def connect():
    conn = sqlite3.connect(os.path.join(os.environ["HOME"], '.Yuki/Storage/impressions.db') )
    return conn

@celeryapp.task
def task_exec_impression(impressions, machine_uuid):
    jobs = []
    for impression_uuid in impressions.split(" "):
        job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression_uuid)
        job = VJob(job_path, machine_uuid)
        jobs.append(job)
    workflow = VWorkflow(jobs, None)
    workflow.run()

@celeryapp.task
def task_update_workflow_status(workflow_id):
    workflow = VWorkflow([], workflow_id)
    workflow.update_workflow_status()


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':

        tarname = request.form["tarname"]
        storage_path = os.path.join(os.environ["HOME"], ".Yuki/Storage")
        request.files[tarname].save(os.path.join("/tmp", tarname))

        tar = tarfile.open(os.path.join("/tmp", tarname),"r")
        for ti in tar:
            tar.extract(ti, os.path.join(storage_path, tarname[:-7]))
        tar.close()

        config = request.form['config']
        logger.info(config)
        request.files[config].save(os.path.join(storage_path, tarname[:-7], config))
    return "Successful"
    # FIXME should check whether the upload is successful or not

@app.route('/execute', methods=['GET', 'POST'])
def execute():
    if request.method == 'POST':
        machine = request.form["machine"]
        contents = request.files["impressions"].read().decode()
        task = task_exec_impression.apply_async(args=[contents, machine])
        for impression in contents.split(" "):
            job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
            VJob(job_path, machine).set_runid(task.id)
        return task.id

    # FIXME should check whether the upload is successful or not


@app.route("/download/<filename>", methods=['GET'])
def download_file(filename):
    directory = os.path.join(os.getcwd(), "data")  # Assuming in the current directory
    return send_from_directory(directory, filename, as_attachment=True)

@app.route("/status/<impression>", methods=['GET'])
def status(impression):
    job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    runner_config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
    runner_config_file = ConfigFile(runner_config_path)
    runners = runner_config_file.read_variable("runners", [])
    runners_id = runner_config_file.read_variable("runners_id", {})

    config_file = ConfigFile(os.path.join(job_path, "config.json"))
    object_type = config_file.read_variable("object_type", "")

    for machine in runners:
        machine_id = runners_id[machine]

        if object_type == "":
            return "empty"

        job = VJob(job_path, machine_id)
        # FIXME: workflow should not be initialized like this
        workflow = VWorkflow([], job.workflow_id())
        status = workflow.status()
        if status != "finished" and status != "failed":
            task_update_workflow_status.apply_async(args=[workflow.uuid])

        if status != "unknown":
            return status

        if os.path.exists(job_path):
            return "deposited"

    return "empty"

@app.route("/status/<impression>/<machine>", methods=['GET'])
def runstatus(impression):
    # FIXME, should updated as status()
    job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    runner_config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
    runner_config_file = ConfigFile(runner_config_path)
    runners = runner_config_file.read_variable("runners", [])
    runners_id = runner_config_file.read_variable("runners_id", {})

    config_file = ConfigFile(os.path.join(job_path, "config.json"))
    object_type = config_file.read_variable("object_type", "")
    machine_id = runners_id[machine]

    if object_type == "":
        return "empty"

    # results = task_exec_impression.AsyncResult(runid)
    job = VJob(job_path, machine_id)
    # FIXME: workflow should not be initialized like this
    workflow = VWorkflow([], job.workflow_id())
    return workflow.status()

    if os.path.exists(job_path):
        return "deposited"

    return "empty"

@app.route("/deposited/<impression>", methods=['GET'])
def deposited(impression):
    job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    if os.path.exists(job_path):
        return "TRUE"
    return "FALSE"



@app.route("/serverstatus", methods=['GET'])
def serverstatus():
    return "ok"

@app.route("/run/<impression>/<machine>", methods=['GET'])
def run(impression, machine):
    # We need to save the run id to the id of job
    logger.info("Trying to run it")
    task = task_exec_impression.apply_async(args=[impression, machine])
    job_path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    VJob(job_path, machine).set_runid(task.id)
    logger.info("Run id = " + task.id)
    return task.id

@app.route("/register_machine/<machine>/<machine_id>", methods=['GET'])
def register_machine(machine, machine_id):
    # FIXME: runner_id -> runners_id
    config_path = os.path.join(os.environ["HOME"], ".Yuki/", "config.json")
    config_file = ConfigFile(config_path)
    runners = config_file.read_variable("runners", [])
    runner_id = config_file.read_variable("runner_id", {})
    runners.append(machine)
    runner_id[machine] = machine_id
    config_file.write_variable("runners", runners)
    config_file.write_variable("runner_id", runner_id)

@app.route("/machine_id/<machine>", methods=["GET"])
def machine_id(machine):
    # print some debug information
    config_path = os.path.join(os.environ["HOME"], ".Yuki/", "config.json")
    config_file = ConfigFile(config_path)
    runner_id = config_file.read_variable("runners_id", {})
    return runner_id[machine]

# @app.route("/runstatus/<impression>", method=['GET'])
# def runstatus(impression):
#     #task_id = get_run_id(impression)
#     # long_task.AsyncResult(task_id)
#     #return statu0s
#     return ""

@app.route("/outputs/<impression>", methods=['GET'])
def outputs(impression):
    path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    job = VJob(path)
    if job.job_type() == "container":
        return " ".join(VContainer(path).outputs())
    return ""

@app.route("/getfile/<impression>/<filename>", methods=['GET'])
def get_file(impression, filename):
    path = os.path.join(os.environ["HOME"], ".Yuki/Storage", impression)
    job = VJob(path)
    if job.job_type() == "container":
        return VContainer(path).get_file(filename)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        conn = connect()
        c = conn.cursor()
        cursor = c.execute("SELECT ID, STATUS from IMPRESSIONS")
        impressions = []
        for row in cursor:
            impression = {}
            impression["id"] = row[0]
            impression["status"] = row[1]
            impression["size"] = "unknown"
            impressions.append(impression)
        conn.commit()
        conn.close()

        return render_template('index.html', impressions=impressions)

    return redirect(url_for('index'))

def start_flask_app():
    app.run(
        host='127.0.0.1',
        port= 3315,
        debug= False,
        )

def start_celery_worker():
    argv = ["-A", "Yuki.server.celeryapp", "worker", "--loglevel=info"]
    celeryapp.worker_main(argv)

def server_start():
    daemon_path = os.path.join(os.environ["HOME"], ".Yuki/daemon")
    print(colorize("[*[*[* Starting the Data Integration Thought Entity *]*]*]", "title0"))

    # with daemon.DaemonContext(
    #     pidfile=pidfile.TimeoutPIDLockFile(daemon_path + "/server.pid"),
    #     stderr=open(daemon_path + "/server.log", "w+"),
    #     ):

    flask_process = Process(target=start_flask_app)
    celery_process = Process(target=start_celery_worker)

    flask_process.start()
    celery_process.start()

    flask_process.join()
    celery_process.join()

    
def stop():
    if status() == "stop":
        return
    daemon_path = os.path.join(os.environ["HOME"], ".Yuki/daemon")
    subprocess.call("kill {}".format(open(daemon_path + "/server.pid").read()), shell=True)
    subprocess.call("kill {}".format(open(daemon_path + "/runner.pid").read()), shell=True)
