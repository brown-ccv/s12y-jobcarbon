#!/usr/bin/env python3

import yaml
import os
from argparse import ArgumentParser
from jobcarbon import (
    total_cpu_seconds,
    user_cpu_seconds,
    system_cpu_seconds,
    parse_responses,
)


def parse_csv(csv_path: str):
    jobs = []
    with open(csv_path) as csv:
        # Get data without header
        data = csv.readlines()[1:]
        for row in data:
            try:
                parsed_row = [int(field) for field in row.strip().split(",")]
                if len(parsed_row) == 5:
                    jobs.append(parsed_row)
            # Skip rows that failed to format.
            except:
                continue
    return jobs


def job_yaml_template(job_id: int, memory: int, cores: int, inputs):
    return {
        "name": f"job{job_id}",
        "description": f"Generate emissions for job {job_id}",
        "tags": {},
        "aggregation": {
            "metrics": [
                "energy",
                "carbon/operation",
                "carbon/embodied",
                "carbon",
                "sci",
            ],
            "type": "both",
        },
        "initialize": {},
        "tree": {
            "children": {
                f"job{job_id}": {
                    "defaults": {
                        "grid/carbon-intensity": 381,
                        "cores-allocated": cores,
                        "memory": memory,
                        "job": 1,
                    },
                    "pipeline": {
                        "observe": {},
                        "regroup": {},
                        "compute": [
                            "utilization",
                            "cpu-lookup",
                            "usage-to-wattage",
                            "duration-to-hours",
                            "watts-to-kw",
                            "calculate-energy",
                            "sci-o",
                            "sci-m",
                            "sum-carbon",
                            "sci",
                        ],
                    },
                    "inputs": inputs,
                }
            }
        },
    }


def build_yaml_for_job(job, outputdir: str):
    job_id = job[0]
    start = job[1]
    end = job[2]
    cores = job[3]
    memory = job[4]
    print(job_id, end="\t")
    try:
        cpu_total_resp = total_cpu_seconds(job_id, start, end)
        cpu_user_resp = user_cpu_seconds(job_id, start, end)
        cpu_system_resp = system_cpu_seconds(job_id, start, end)
        inputs = parse_responses(cpu_system_resp, cpu_total_resp, cpu_user_resp)
        job_yaml = job_yaml_template(job_id, memory, cores, inputs)
        with open(os.path.join(outputdir, f"{job_id}.yml"), "w") as yml_file:
            yml_file.write(yaml.dump(job_yaml))
        print("Finished writing yaml for", job_id)
    except Exception as e:
        print("Error creating yaml for id", job_id)
        print(e)


def main():
    parser = ArgumentParser(
        description="generate observations for impact framework in batch"
    )
    parser.add_argument("jobscsv", help="CSV of jobs pulled from SlurmDump")
    parser.add_argument("outputdir", help="directory for completed manifests")
    args = parser.parse_args()

    jobs = parse_csv(args.jobscsv)
    try:
        os.rmdir(args.outputdir)
    except:
        pass
    os.mkdir(args.outputdir)

    for job in jobs:
        print(job, "🔄")
        build_yaml_for_job(job, args.outputdir)
        print(job[0], "✅")


if __name__ == "__main__":
    main()

# cpu_total_resp = total_cpu_seconds(args.jobid, args.start, args.end)
# cpu_user_resp = user_cpu_seconds(args.jobid, args.start, args.end)
# cpu_system_resp = system_cpu_seconds(args.jobid, args.start, args.end)
# print(yaml.dump(parse_responses(cpu_system_resp, cpu_total_resp, cpu_user_resp)))
