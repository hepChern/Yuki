"""Yuki CLI entry point."""
import os
import subprocess

import click

from .server_main import server_start
from .server_main import stop as server_stop
from .server_main import status as server_status
from Yuki.utils.env_interpreter import EnvInterpreter

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

# ------ Run workflow ------ #
@cli.command('run-workflow')
@click.argument('workflow_uuid')
@click.option('--cores', '-j', default='all', show_default=True,
              help='Number of cores to pass to snakemake.')
def run_workflow(workflow_uuid, cores):
    """Run a local workflow by its UUID with status tracking.

    For dry-run workflows this changes to the local execution directory
    ($YUKIDIR/LocalWorkflows/<UUID>) and invokes snakemake with the
    conda backend enabled. Status is tracked and written to
    $YUKIDIR/Workflows/<project>/<uuid>/results.json for integration
    with the Flask API.
    """
    import json
    from Yuki.kernel.snakemake_monitor import SnakemakeMonitor

    yuki_home = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))

    local_exec_dir = os.path.join(yuki_home, "LocalWorkflows", workflow_uuid)
    if not os.path.isdir(local_exec_dir):
        click.echo(f"Workflow {workflow_uuid} not found.")
        raise click.ClickException(f"Workflow {workflow_uuid} not found.")

    snakefile_path = os.path.join(local_exec_dir, "Snakefile")
    if not os.path.exists(snakefile_path):
        click.echo(f"No Snakefile found in {local_exec_dir}")
        raise click.ClickException(f"No Snakefile found in {local_exec_dir}")

    # Find the workflow in the Workflows directory to get project_uuid
    workflows_dir = os.path.join(yuki_home, "Workflows")
    workflow_path = None
    project_uuid = None

    if os.path.isdir(workflows_dir):
        for proj_dir in os.listdir(workflows_dir):
            proj_path = os.path.join(workflows_dir, proj_dir)
            if not os.path.isdir(proj_path):
                continue
            potential_workflow = os.path.join(proj_path, workflow_uuid)
            if os.path.isdir(potential_workflow):
                workflow_path = potential_workflow
                project_uuid = proj_dir
                break

    if not workflow_path:
        click.echo(f"Workflow {workflow_uuid} not found in $YUKIDIR/Workflows/")
        raise click.ClickException(f"Workflow not found")

    # Create logger function
    def logger(msg):
        timestamp = os.popen('date +"%Y-%m-%d %H:%M:%S"').read().strip()
        click.echo(f"[{timestamp}] {msg}")

    # Initialize monitor and execute snakemake
    monitor = SnakemakeMonitor(workflow_path, local_exec_dir)
    click.echo(f"Running snakemake in {local_exec_dir}")
    click.echo(f"Status updates will be written to {workflow_path}/results.json")

    exit_code = monitor.execute_snakemake(cores, logger)

    if exit_code == 0:
        click.echo(f"Workflow {workflow_uuid} completed successfully")
        click.echo(f"View status at: /status/{project_uuid}/<impression>")
    else:
        click.echo(f"Workflow {workflow_uuid} failed with exit code {exit_code}")
        click.echo(f"Check logs at {workflow_path}/log.json")

    return exit_code


# ------ Environment Map ------ #
@cli.group()
def env_map():
    """Manage conda_env_map environment re-interpretations."""


@env_map.command('add')
@click.argument('source', type=str)
@click.argument('env_type', type=str)
@click.argument('value', type=str)
def env_map_add(source, env_type, value):
    """Add or update an environment mapping.

    SOURCE is the original environment string (e.g. docker:img).
    TYPE is the target type (e.g. conda).
    VALUE is the target environment value (e.g. env.yaml).
    """
    yuki_home = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
    config_path = os.path.join(yuki_home, "config.json")
    try:
        EnvInterpreter.add_mapping(config_path, source, env_type, value)
        click.echo(f"Mapped '{source}' -> {env_type}:{value}")
    except (ValueError, TypeError) as e:
        raise click.ClickException(str(e)) from e


@env_map.command('list')
def env_map_list():
    """List all environment mappings."""
    yuki_home = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
    config_path = os.path.join(yuki_home, "config.json")
    try:
        mappings = EnvInterpreter.list_mappings(config_path)
    except (ValueError, TypeError) as e:
        raise click.ClickException(str(e)) from e
    if not mappings:
        click.echo("No environment mappings configured.")
        return
    for source, entry in mappings.items():
        click.echo(f"{source} -> {entry['type']}:{entry['value']}")


@env_map.command('remove')
@click.argument('source', type=str)
def env_map_remove(source):
    """Remove an environment mapping.

    SOURCE is the original environment string to unmap.
    """
    yuki_home = os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))
    config_path = os.path.join(yuki_home, "config.json")
    try:
        EnvInterpreter.remove_mapping(config_path, source)
        click.echo(f"Removed mapping for '{source}'.")
    except (ValueError, TypeError) as e:
        raise click.ClickException(str(e)) from e


# Main
def main():
    """Main entry point for Yuki CLI."""
    cli()  # pylint: disable=no-value-for-parameter
