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
from yamldump import dump


def parse_csv(csv_path: str):
    jobs = []
    with open(csv_path) as csv:
        # Get data without header
        data = csv.readlines()[1:]
        for row in data:
            try:
                parsed_row = row.strip().split("\t")
                if len(parsed_row) == 4:
                    jobs.append(parsed_row)
            # Skip rows that failed to format.
            except:
                continue
    return jobs


def job_yaml_template(job_id: int, memory: int, cores: int, inputs):
    return {
        "name": f"job{job_id}",
        "description": f"Generate emissions for job {job_id}",
        "tags": None,
        "aggregation": {
            "metrics": [
                "duration",
                "energy",
                "carbon/operational",
                "carbon/embodied",
                "carbon",
                "sci",
            ],
            "type": "both",
        },
        "initialize": {
            "plugins": {
                "utilization": {
                    "path": "builtin",
                    "method": "Divide",
                    "config": {
                        "numerator": "cpu/user-seconds",
                        "denominator": "cpu/total-seconds",
                        "output": "cpu/utilization",
                    },
                },
                "cpu-lookup": {
                    "path": "builtin",
                    "method": "CSVLookup",
                    "config": {
                        "filepath": "./resources/cpu-model.csv",
                        "query": {"node": "node-name"},
                        "output": "*",
                    },
                },
                "usage-to-wattage": {
                    "path": "builtin",
                    "method": "Multiply",
                    "config": {
                        "input-parameters": ["cpu/utilization", "watts-per-core"],
                        "output-parameter": "wattage-scaled",
                    },
                },
                "duration-to-hours": {
                    "path": "builtin",
                    "method": "Coefficient",
                    "config": {
                        "input-parameter": "duration",
                        "coefficient": "=1/60",
                        "output-parameter": "duration-hours",
                    },
                },
                "watts-to-kw": {
                    "path": "builtin",
                    "method": "Coefficient",
                    "config": {
                        "input-parameter": "wattage-scaled",
                        "coefficient": "=1/1000",
                        "output-parameter": "kilowatts",
                    },
                },
                "calculate-energy": {
                    "path": "builtin",
                    "method": "Multiply",
                    "config": {
                        "input-parameters": ["duration-hours", "kilowatts"],
                        "output-parameter": "energy",
                    },
                },
                "sci-o": {
                    "path": "builtin",
                    "method": "Multiply",
                    "config": {
                        "input-parameters": ["grid/carbon-intensity", "energy"],
                        "output-parameter": "carbon/operational",
                    },
                    "parameter-metadata": {
                        "outputs": {
                            "carbon/operational": {
                                "description": "Total operational carbon",
                                "unit": "gCO2eq/KWh",
                                "aggregation-method": {
                                    "time": "sum",
                                    "component": "sum",
                                },
                            }
                        }
                    },
                },
                "sci-m": {
                    "path": "builtin",
                    "method": "SciEmbodied",
                    "config": {
                        "lifespan": 1.578e8,
                        "duration": 60,
                        "output-parameter": "carbon/embodied",
                    },
                    "mapping": {"vCPUs": "cores-allocated"},
                    "parameter-metadata": {
                        "outputs": {
                            "carbon/embodied": {
                                "description": "Total embodied carbon",
                                "unit": "gCO2eq",
                                "aggregation-method": {
                                    "time": "sum",
                                    "component": "sum",
                                },
                            }
                        }
                    },
                },
                "sum-carbon": {
                    "path": "builtin",
                    "method": "Sum",
                    "config": {
                        "input-parameters": ["carbon/operational", "carbon/embodied"],
                        "output-parameter": "carbon",
                    },
                },
                "sci": {
                    "path": "builtin",
                    "method": "Sci",
                    "config": {"functional-unit": "job"},
                },
            }
        },
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
                        "observe": None,
                        "regroup": None,
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


allocation_keys = {
    "1": "cpu",
    "2": "mem",
    "3": "energy",
    "4": "node",
    "5": "billing",
    "6": "fs",
    "7": "vmem",
    "8": "pages",
    "1000": "dynamic_offset",
    "1001": "gpu",
    "1002": "gpumem",
    "1003": "gpuutil",
}


def parse_alloc(alloc_str: str):
    parsed_allocs = {}
    allocs = alloc_str.replace('"', "").split(",")
    for alloc in allocs:
        try:
            key_value_split = alloc.split("=")
            parsed_allocs[allocation_keys[key_value_split[0]]] = int(key_value_split[1])
        except:
            continue
    if not "cpu" in parsed_allocs.keys() and not "mem" in parsed_allocs.keys():
        raise Exception("Failed to find node and memory allocations in job information")
    return parsed_allocs


def build_yaml_for_job(job, outputdir: str):
    job_id = int(job[0])
    start = int(job[1])
    end = int(job[2])
    tres_alloc = job[3]
    print(job_id, end="... ")
    try:
        allocations = parse_alloc(tres_alloc)
        cpu_total_resp = total_cpu_seconds(job_id, start, end)
        cpu_user_resp = user_cpu_seconds(job_id, start, end)
        cpu_system_resp = system_cpu_seconds(job_id, start, end)
        inputs = parse_responses(cpu_system_resp, cpu_total_resp, cpu_user_resp)
        job_yaml = job_yaml_template(
            job_id, allocations["mem"], allocations["cpu"], inputs
        )
        with open(os.path.join(outputdir, f"{job_id}.yml"), "w") as yml_file:
            yml_file.write(dump(job_yaml))
        print("✅")
    except Exception as e:
        print("💀")
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
        build_yaml_for_job(job, args.outputdir)


if __name__ == "__main__":
    main()
