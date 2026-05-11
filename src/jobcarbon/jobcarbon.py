#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
from typing import Annotated

import requests
import typer

from .config import Config
from .engine import PrometheusEngine
from .generator import generate_manifest
from .loader import process_job
from .utils import output_text
from .yamldump import dump

app = typer.Typer()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _run_job(
    engine: PrometheusEngine, jobid: str, output: Path | None, config: Config
) -> None:
    """Run a single job manifest generation."""
    node_data = process_job(engine, jobid, config.lookback_days)
    manifest = generate_manifest(jobid, node_data, config)
    content = dump(manifest)
    output_text(content, output)


@app.command("create-config")
def create_config(
    output: Annotated[
        Path | None, typer.Option(help="output file (default: stdout)")
    ] = None,
) -> None:
    """Generate jobcarbon.toml from sinfo output.

    Usage: sinfo -h -o "%N %G" | jobcarbon create-config
    """
    if sys.stdin.isatty():
        logger.error(
            'No piped data. Use: sinfo -h -o "%%N %%G" | jobcarbon create-config'
        )
        raise typer.Exit(1)

    content = Config.generate(sys.stdin.readlines())
    output_text(content, output)


@app.command()
def batch(
    job_ids: Annotated[list[str], typer.Argument(help="list of Slurm job ids")],
    output_dir: Annotated[
        str | None,
        typer.Option(help="output directory for manifest files (default: ./)"),
    ] = None,
    embodied: Annotated[
        bool, typer.Option("--embodied", help="include embodied carbon estimate")
    ] = False,
) -> None:
    """Generate multiple manifests in one pass"""
    output_path = Path.cwd()
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

    config = Config.load(embodied=embodied)
    engine = PrometheusEngine(config)

    for job_id in job_ids:
        try:
            _run_job(engine, job_id, output_path / f"job{job_id}.yaml", config)
            logger.info("%s ok", job_id)
        except (ValueError, requests.RequestException) as e:
            logger.error("%s failed: %s", job_id, e)


# TODO(@broarr): Rename to manifest instead of run to avoid confusion with `if-run`
@app.command()
def run(
    job_id: Annotated[str, typer.Argument(help="Slurm job id")],
    output: Annotated[
        str | None, typer.Option(help="output file for manifest (default: stdout)")
    ] = None,
    embodied: Annotated[
        bool, typer.Option("--embodied", help="include embodied carbon estimate")
    ] = False,
) -> None:
    """Generate an IF manifest for a Slurm job"""
    config = Config.load(embodied=embodied)
    engine = PrometheusEngine(config)
    output_path = Path.cwd() / output if output else None
    _run_job(engine, job_id, output_path, config)

# TODO(@broarr): Add some help text about how to run `if-run` on a manifest

if __name__ == "__main__":
    app()
