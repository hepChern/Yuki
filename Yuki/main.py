""" """
import click
import os
from Yuki.kernel.VContainer import VContainer
from Yuki.kernel.VJob import VJob
from Yuki.kernel.VImage import VImage

# from Yuki.register import register as machine_register

from Yuki.server_main import server_start
from Yuki.server_main import stop as server_stop
from Yuki.server_main import status as server_status

@click.group()
@click.pass_context
def cli(ctx):
    """ Chern command only is equal to `Chern ipython`
    """
    pass

@cli.command()
def register():
    """ Register the running machine
    """
    machine_register()

def connections():
    pass

# ------ Server ------ #
@cli.group()
def server():
    pass

@server.command()
def start():
    server_start()

@server.command()
def stop():
    server_stop()

@server.command()
def status():
    server_status()

@cli.command()
@click.argument("path")
def execute(path):
    # job = create_job_instance(path)
    job = VJob(path)
    if job.is_zombie():
        return
    print(job.job_type())
    if job.job_type() == "container":
        job = VContainer(path)
    else:
        job = VImage(path)
    job.execute()

@cli.command()
@click.argument("impression")
@click.argument("path")
def feed(impression, path):
    from Yuki.feeder import feed as cli_feed
    cli_feed(impression, path)

# Main
def main():
    cli()

