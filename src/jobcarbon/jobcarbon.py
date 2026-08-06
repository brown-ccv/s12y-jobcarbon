#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
from typing import Annotated

import requests
import typer

from .config import Config, parse_hostlist
from .embodied import node_embodied
from .engine import PrometheusEngine
from .generator import generate_manifest
from .loader import probe_node, process_job
from .validate import find_problems
from .yamldump import dump

app = typer.Typer()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _run_job(
    engine: PrometheusEngine, jobid: str, output: Path | None, config: Config
) -> None:
    """Run a single job manifest generation."""
    node_data = process_job(engine, jobid, config)
    manifest = generate_manifest(jobid, node_data, config)
    content = dump(manifest)
    if output:
        output.write_text(content)
    else:
        print(content, end="")


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
    """Generate multiple manifests in one pass.

    See `jobcarbon manifest --help` for more details on Impact Framework
    and manifest files.
    """
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


@app.command()
def manifest(
    job_id: Annotated[str, typer.Argument(help="Slurm job id")],
    output: Annotated[
        str | None, typer.Option(help="output file for manifest (default: stdout)")
    ] = None,
    embodied: Annotated[
        bool, typer.Option("--embodied", help="include embodied carbon estimate")
    ] = False,
) -> None:
    """Generate an IF manifest for a Slurm job.

    Impact Framework manifests can be used to compute carbon intensity
    by using the `if-run` tool. In Oscar run `module load impact-
    framework` to load Impact Framework into your current session or run
    `npm install -g @grnsft/if` to install Impact Framework locally. See
    https://if.greensoftware.foundation
    for more details on Impact Framework.
    """
    config = Config.load(embodied=embodied)
    engine = PrometheusEngine(config)
    output_path = Path.cwd() / output if output else None
    _run_job(engine, job_id, output_path, config)


@app.command()
def embodied(
    hostlists: Annotated[
        list[str],
        typer.Argument(help="Slurm hostlists, e.g. gpu4001 gpu[4001-4008] node2201"),
    ],
) -> None:
    """Estimate the embodied carbon of one or more nodes' hardware.

    Probes each node's current socket count, memory, and GPU count from
    Prometheus and prints the full (unattributed) embodied carbon of its
    CPUs, DRAM, and GPUs, one tab-separated row per node, plus a totals row.
    A calculator for validation and curiosity — it does not generate an
    Impact Framework manifest.
    """
    config = Config.load(embodied=True)
    engine = PrometheusEngine(config)

    # Tab-separated, one row per node; units in the header. grep/cut friendly.
    rows = [
        "node\tsockets\tmem_gib\tgpus\tcpu_gco2eq\tdram_gco2eq\tgpu_gco2eq\ttotal_gco2eq"
    ]
    sockets = mem = gpus = cpu = dram = gpu = total = 0.0
    names = [name for hl in hostlists for name in parse_hostlist(hl)]
    for name in names:
        try:
            socket_count, mem_total, gpu_count = probe_node(engine, name)
            r = node_embodied(name, socket_count, mem_total, gpu_count, config)
        except (ValueError, requests.RequestException) as e:
            logger.error("%s failed: %s", name, e)
            continue
        rows.append(
            f"{name}\t{socket_count}\t{mem_total}\t{gpu_count}"
            f"\t{r['cpu']:.0f}\t{r['dram']:.0f}\t{r['gpu']:.0f}\t{r['total']:.0f}"
        )
        sockets += socket_count
        mem += mem_total
        gpus += gpu_count
        cpu += r["cpu"]
        dram += r["dram"]
        gpu += r["gpu"]
        total += r["total"]

    if len(rows) == 1:  # header only — every node failed
        raise typer.Exit(1)

    rows.append(
        f"total\t{sockets:.0f}\t{mem:.0f}\t{gpus:.0f}"
        f"\t{cpu:.0f}\t{dram:.0f}\t{gpu:.0f}\t{total:.0f}"
    )
    print("\n".join(rows))


@app.command(name="validate-config")
def validate_config() -> None:
    """Check the resolved config loads and is complete (offline, no cluster).

    A post-install sanity check: confirms the CLI runs, the config file is
    discoverable and parses, and every hardware entry is well-formed (every GPU
    node also has a CPU die area, scalars resolve). Exits non-zero on any
    problem so it can gate an install.
    """
    try:
        config = Config.load()
    except (FileNotFoundError, ValueError) as e:
        logger.error("config could not be loaded: %s", e)
        raise typer.Exit(1)

    problems = find_problems(config)
    if problems:
        for problem in problems:
            logger.error("%s", problem)
        logger.error("config invalid: %d problem(s)", len(problems))
        raise typer.Exit(1)

    logger.info(
        "config ok: %d CPU model(s), %d GPU model(s)",
        len({s["cpu_model"] for s in config.cpu_node_map.values()}),
        len({s["gpu_model"] for s in config.node_map.values()}),
    )


if __name__ == "__main__":
    app()
