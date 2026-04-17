#!/usr/bin/env python3
import logging
import re
import sys
from pathlib import Path
from typing import Annotated

import requests
import typer

from engine import PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump

app = typer.Typer()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _run_job(engine: PrometheusEngine, jobid: str, output: Path | None) -> None:
    """Fetch telemetry for jobid and write the manifest to output path, or stdout if None"""
    node_data = process_job(engine, jobid)
    manifest = generate_manifest(jobid, node_data)
    content = dump(manifest)
    if output is None:
        print(content, end="")
        return
    output.write_text(content)


def _parse_hostlist(hostlist: str) -> list[str]:
    """Parses Slurm hostlist strings"""
    # Single host e.g. "gpu1"
    single_host = re.compile(r"^[a-z]+\d+$")
    if single_host.match(hostlist):
        return [hostlist]

    # Prefixed list/range e.g. "gpu[1,3-4]" or "gpu[1-3]"
    match = re.match(r"^([a-z]+)\[(.*)\]$", hostlist)
    if not match:
        raise ValueError(f"Unable to parse hostlist: {hostlist}")

    prefix = match.group(1)
    host_patterns = match.group(2)

    hosts: list[str] = []
    for pattern in host_patterns.split(','):
        if '-' not in pattern:
            # single number
            hosts.append(f"{prefix}{pattern}")
            continue
        start, end = pattern.split('-', 1)
        for i in range(int(start), int(end) + 1):
            hosts.append(f"{prefix}{i}")

    return hosts
        

@app.command()
def init() -> None:
    """Generate config approperiate for cluster, reads sinfo from stdin"""
    if sys.stdin.isatty():
        logger.error("No piped data found. Use:  sinfo -h -o \"%N %G\" | jobcarbon init")
        return

    # NOTE(@broarr): This is hard coded to the GRES column in Oscar's Slurm database
    #   Ideally this would be configurable. Some clusters have VRAM size as a feature,
    #   Oscar does not. We need an explicit map. This will be dependent on cluster config
    gpu_vram_mib = {
        "a2": 15356,
        "quadro_rtx_6000": 24576,
        "geforce3090": 24576,
        "nvidia_geforce_rtx_3090": 24576,
        "nvidia_rtx_a5000": 24564,
        "a5500": 23028,
        "nvidia_a40": 46068,
        "nvidia_rtx_a6000": 49140,
        "a6000": 46068,
        "l40": 46068,
        "l40s": 46068,
        "a100": 81920,
        "nvidia_h100_nvl": 95830,
        "h100": 81559,
        "nvidia_gh200_480gb": 97871,
        "nvidia_b200": 183359
    }

    for line in sys.stdin.readlines():
        pass

    

@app.command()
def batch(
    job_ids: Annotated[list[str], typer.Argument(help="list of Slurm job ids")],
    output_dir: Annotated[
        str | None,
        typer.Option(help="output directory for manifest files (default: ./)"),
    ] = None,
) -> None:
    """Generate multiple manifests in one pass"""
    output_path = Path.cwd()
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

    engine = PrometheusEngine()

    for job_id in job_ids:
        try:
            _run_job(engine, job_id, output_path / f"job{job_id}.yaml")
            logger.info("%s ok", job_id)
        except (ValueError, requests.RequestException) as e:
            logger.error("%s failed: %s", job_id, e)


@app.command()
def main(
    job_id: Annotated[str, typer.Argument(help="Slurm job id")],
    output: Annotated[
        str | None, typer.Option(help="output file for manifest (default: stdout)")
    ] = None,
) -> None:
    """CLI entry point: generate IMP manifests for one or more Slurm jobs"""

    engine = PrometheusEngine()
    output_path = None
    if output:
        output_path = Path.cwd() / output

    _run_job(engine, job_id, output_path)


if __name__ == "__main__":
    app()
