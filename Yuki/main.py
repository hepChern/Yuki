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
@click.option('--dev-dir',
              help='Mount local Yuki source directory as /app/Yuki for development.')
def docker_run(image, yuki_dir, port, dev_dir):
    """Run a Yuki Docker container.

    IMAGE is the Docker image name (default: yuki:latest).
    """
    yuki_dir = os.path.expanduser(yuki_dir)
    cmd = [
        'docker', 'run', '-it', '-d',
        '-v', f'{yuki_dir}:/root/.Yuki',
        '-p', f'{port}:3315',
    ]
    if dev_dir:
        dev_dir = os.path.expanduser(dev_dir)
        if not os.path.isdir(dev_dir):
            raise click.ClickException(f"Development directory does not exist: {dev_dir}")
        cmd.extend(['-v', f'{dev_dir}:/mnt/yuki-source:ro'])
    cmd.append(image)
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


@docker.command('restart')
@click.argument('container', required=False)
def docker_restart(container):
    """Sync mounted source into a running Yuki container and restart the server.

    If CONTAINER is omitted, uses the first running container with
    /mnt/yuki-source mounted (or the yuki-dev container name).
    """
    if container is None:
        result = subprocess.run(
            ['docker', 'ps', '--filter', 'volume=/mnt/yuki-source',
             '--format', '{{.Names}}'],
            capture_output=True, text=True
        )
        containers = result.stdout.strip().split('\n')
        containers = [c for c in containers if c]
        if not containers:
            result = subprocess.run(
                ['docker', 'ps', '--filter', 'name=yuki-dev',
                 '--format', '{{.Names}}'],
                capture_output=True, text=True
            )
            containers = result.stdout.strip().split('\n')
            containers = [c for c in containers if c]
        if not containers:
            raise click.ClickException(
                "No running Yuki dev container found. "
                "Specify one: yuki docker restart <container>"
            )
        container = containers[0]

    click.echo(f"Syncing source into {container}...")
    subprocess.run(
        ['docker', 'exec', container, 'cp', '-r', '/mnt/yuki-source/.', '/app/Yuki/'],
        check=True
    )
    click.echo("Restarting container...")
    subprocess.run(
        ['docker', 'restart', container],
        check=True
    )
    click.echo("Done.")

# ------ Run workflow ------ #
@cli.command('run-workflow')
@click.argument('workflow_uuid')
@click.option('--cores', '-j', default='all', show_default=True,
              help='Number of cores to pass to snakemake.')
def run_workflow(workflow_uuid, cores):
    """Run a local workflow by its UUID with file staging and status tracking.

    For dry-run workflows this:
    1. Stages in files from Storage to LocalWorkflows using hard links
    2. Executes snakemake with conda environment support
    3. Stages out results back to Storage
    4. Tracks status in the Flask API via results.json

    Files are copied using hard links when on the same filesystem for
    performance, with automatic fallback to regular copy for cross-filesystem.
    """
    import json
    from Yuki.kernel.snakemake_monitor import SnakemakeMonitor
    from Yuki.kernel.file_staging import FileStager

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

    logger(f"Workflow UUID: {workflow_uuid}")
    logger(f"Project UUID: {project_uuid}")
    logger(f"Workflow path: {workflow_path}")
    logger(f"Execution path: {local_exec_dir}")

    # Stage in files with hard links
    logger("[STAGE_IN] Starting file staging...")
    stager = FileStager(workflow_path, local_exec_dir, project_uuid, logger)
    if not stager.stage_in():
        click.echo("File staging failed")
        raise click.ClickException("File staging failed")

    # Initialize monitor and execute snakemake
    monitor = SnakemakeMonitor(workflow_path, local_exec_dir)
    logger(f"[SNAKEMAKE] Running snakemake with {cores} cores")

    exit_code = monitor.execute_snakemake(cores, logger)

    # Stage out results
    if exit_code == 0:
        logger("[STAGE_OUT] Starting result collection...")
        if not stager.stage_out():
            logger("Result collection failed (but workflow succeeded)")
        else:
            logger("[STAGE_OUT] Results successfully collected")

    # Final status
    if exit_code == 0:
        click.echo(f"\n✓ Workflow {workflow_uuid} completed successfully")
        click.echo(f"  Results stored in: {workflow_path}/results.json")
        click.echo(f"  Output files in: ~/.Yuki/Storage/{project_uuid}/*/stageout/")
    else:
        click.echo(f"\n✗ Workflow {workflow_uuid} failed with exit code {exit_code}")
        click.echo(f"  Check logs at: {workflow_path}/log.json")

    return exit_code


# ------ Impression Import/Export ------ #
@cli.command('impression-export')
@click.argument('impressions', nargs=-1, required=True)
@click.option('--project-uuid', required=True,
              help='Project UUID that the impressions belong to.')
@click.option('--output', '-o', required=True,
              help='Output tar.gz file path.')
@click.option('--yuki-dir', '-d', envvar='YUKIDIR',
              default='~/.Yuki', show_default=True,
              help='Yuki storage directory (env: YUKIDIR).')
def impression_export(impressions, project_uuid, output, yuki_dir):
    """Export one or more impressions to a tar.gz archive.

    IMPRESSIONS are one or more 32-character hex impression UUIDs.
    """
    from Yuki.kernel.impression_transfer import export_impressions

    yuki_dir = os.path.expanduser(yuki_dir)
    export_impressions(project_uuid, list(impressions), output,
                       yuki_dir=yuki_dir)
    click.echo(f"Exported {len(impressions)} impression(s) to: {output}")


@cli.command('impression-import')
@click.argument('tar_file', type=click.Path(exists=True))
@click.option('--project-uuid', required=True,
              help='Target project UUID to import into.')
@click.option('--yuki-dir', '-d', envvar='YUKIDIR',
              default='~/.Yuki', show_default=True,
              help='Yuki storage directory (env: YUKIDIR).')
def impression_import(tar_file, project_uuid, yuki_dir):
    """Import impressions from a tar.gz archive.

    TAR_FILE is the path to the tar.gz archive.
    """
    from Yuki.kernel.impression_transfer import import_impression

    yuki_dir = os.path.expanduser(yuki_dir)
    result = import_impression(project_uuid, tar_file, yuki_dir=yuki_dir)

    click.echo(f"Imported {result['count']} impression(s): "
               f"{', '.join(result['imported'])}")
    if result['skipped']:
        for s in result['skipped']:
            click.echo(f"  Skipped: {s['name']} — {s['reason']}")


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
