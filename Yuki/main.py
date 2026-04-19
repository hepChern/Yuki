"""Yuki CLI entry point."""
import os
import subprocess

import click

from .server_main import server_start
from .server_main import stop as server_stop
from .server_main import status as server_status

@click.group()
@click.pass_context
def cli(_ctx):
    """ Chern command only is equal to `Chern ipython`
    """

# ------ Server ------ #
@cli.group()
def server():
    """Server management commands."""

@server.command()
def start():
    """Start Yuki server."""
    server_start()

@server.command()
def stop():
    """Stop Yuki server."""
    server_stop()

@server.command()
def status():
    """Check Yuki server status."""
    server_status()

# ------ Docker ------ #
@cli.group()
def docker():
    """Docker management commands."""

@docker.command('run')
@click.argument('image', default='yuki:latest')
@click.option('--yuki-dir', '-d', envvar='YUKIDIR',
              default='~/.Yuki', show_default=True,
              help='Host directory to mount as /root/.Yuki (env: YUKIDIR).')
@click.option('--port', '-p', envvar='YUKIPORT',
              default='3315', show_default=True,
              help='Host port to map to container port 3315 (env: YUKIPORT).')
def docker_run(image, yuki_dir, port):
    """Run a Yuki Docker container.

    IMAGE is the Docker image name (default: yuki:latest).
    """
    yuki_dir = os.path.expanduser(yuki_dir)
    cmd = [
        'docker', 'run', '-it', '-d',
        '-v', f'{yuki_dir}:/root/.Yuki',
        '-p', f'{port}:3315',
        image
    ]
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

# Main
def main():
    """Main entry point for Yuki CLI."""
    cli()  # pylint: disable=no-value-for-parameter
