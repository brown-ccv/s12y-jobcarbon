#!/usr/bin/env python3
from argparse import ArgumentParser

from engine import LOOKBACK_DAYS, PROMETHEUS_URL, PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump

GRID_CARBON_INTENSITY = 381


def main():
    parser = ArgumentParser(description="generate IMP manifest for a single job")
    parser.add_argument("jobid", help="Slurm job ID")
    args = parser.parse_args()

    engine = PrometheusEngine(PROMETHEUS_URL)
    node_data_list = process_job(engine, args.jobid, LOOKBACK_DAYS)
    manifest = generate_manifest(args.jobid, node_data_list, GRID_CARBON_INTENSITY)
    print(dump(manifest))


if __name__ == "__main__":
    main()
