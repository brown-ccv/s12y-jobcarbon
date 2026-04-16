#!/usr/bin/env python3
import logging
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

import requests

from engine import PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump

logger = logging.getLogger(__name__)


def _resolve_output(jobid: str, args: Namespace, batch: bool) -> Path | None:
    """Return the output file path for a job, or None to indicate stdout"""
    if args.output:
        return Path(args.output)
    if args.output_dir:
        return Path(args.output_dir) / f"{jobid}.yml"
    if batch:
        # Multiple job IDs, no --output-dir: write job<id>-carbon.imp in cwd
        return Path(f"job{jobid}-carbon.imp")
    return None


def _run_job(engine: PrometheusEngine, jobid: str, output: Path | None) -> None:
    """Fetch telemetry for jobid and write the manifest to output path, or stdout if None"""
    node_data = process_job(engine, jobid)
    manifest = generate_manifest(jobid, node_data)
    content = dump(manifest)
    if output is None:
        print(content, end="")
        return
    output.write_text(content)


def _run_job_and_report(
    engine: PrometheusEngine, jobid: str, output: Path | None
) -> None:
    """Run a job, logging success or failure to stderr"""
    try:
        _run_job(engine, jobid, output)
        logger.info("%s ok", jobid)
    except (ValueError, requests.RequestException) as e:
        logger.error("%s failed: %s", jobid, e)


def main():
    """CLI entry point: generate IMP manifests for one or more Slurm jobs"""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

    parser = ArgumentParser(description="generate IMP manifests for Slurm jobs")
    parser.add_argument("jobids", nargs="+", help="one or more Slurm job IDs")

    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output",
        metavar="FILE",
        help="write output to FILE (single job only; default: stdout)",
    )
    output_group.add_argument(
        "--output-dir",
        metavar="DIR",
        help="write one <jobid>.yml per job into DIR",
    )
    args = parser.parse_args()

    if args.output and len(args.jobids) > 1:
        parser.error("--output can only be used with a single job ID")

    # Batch mode: multiple job IDs, or an explicit output directory was given
    # In batch mode each job is written to a file and errors are logged
    # In single mode output goes to stdout (or --output file) and errors crash
    batch = len(args.jobids) > 1 or bool(args.output_dir)

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    engine = PrometheusEngine()

    for jobid in args.jobids:
        logger.info("%s...", jobid)
        if batch:
            _run_job_and_report(engine, jobid, _resolve_output(jobid, args, batch))
        else:
            _run_job(engine, jobid, _resolve_output(jobid, args, batch))


if __name__ == "__main__":
    main()
