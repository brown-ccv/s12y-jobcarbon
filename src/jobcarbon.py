#!/usr/bin/env python3
from argparse import ArgumentParser

from engine import PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump


def main():
    parser = ArgumentParser(description="generate IMP manifest for a single job")
    parser.add_argument("jobid", help="Slurm job ID")
    args = parser.parse_args()

    engine = PrometheusEngine()
    node_data_list = process_job(engine, args.jobid)
    manifest = generate_manifest(args.jobid, node_data_list)
    print(dump(manifest))


if __name__ == "__main__":
    main()
