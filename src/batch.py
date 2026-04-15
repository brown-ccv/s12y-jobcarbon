#!/usr/bin/env python3
import os
from argparse import ArgumentParser

from engine import PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump


def main():
    parser = ArgumentParser(description="generate IMP manifests for jobs in batch")
    parser.add_argument("jobscsv", help="file with one job ID per line")
    parser.add_argument("outputdir", help="directory for completed manifests")
    args = parser.parse_args()

    with open(args.jobscsv) as f:
        jobids = [line.strip() for line in f if line.strip()]

    os.makedirs(args.outputdir, exist_ok=True)

    engine = PrometheusEngine()

    for jobid in jobids:
        print(jobid, end="... ", flush=True)
        try:
            node_data_list = process_job(engine, jobid)
            manifest = generate_manifest(jobid, node_data_list)
            out = os.path.join(args.outputdir, f"{jobid}.yml")
            with open(out, "w") as f:
                f.write(dump(manifest))
            print("ok")
        except Exception as e:
            print("failed")
            print(f"  {e}")


if __name__ == "__main__":
    main()
