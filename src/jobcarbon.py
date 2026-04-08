#!/usr/bin/env python3
from argparse import ArgumentParser
from datetime import datetime
from typing import Any

import pandas as pd
import requests

from yamldump import dump


USE_LOCAL_PROMETHEUS = True
PROMETHEUS_SERVER_SLURM = "http://slurm02:9390/api/v1"
PROMETHEUS_SERVER_LOCAL = "http://localhost:9390/api/v1"
PROMETHEUS_SERVER = (
    PROMETHEUS_SERVER_LOCAL if USE_LOCAL_PROMETHEUS else PROMETHEUS_SERVER_SLURM
)
QUERY_RANGE_ENDPOINT = f"{PROMETHEUS_SERVER}/query_range"
QUERY_ENDPOINT = f"{PROMETHEUS_SERVER}/query"
STEP_SECONDS = 60


def get_overall_metric(resp: dict[Any, Any]) -> dict[Any, Any]:
    results = resp["data"]["result"]
    name = None
    overall = {}
    for result in results:
        metric = result["metric"]
        name = get_node_name(result)
        if metric.get("step") or metric.get("task"):
            continue
        overall[name] = result

    if not overall:
        raise KeyError(f"cannot find overall metrics for {name}")

    return overall


def get_metric_name(result: dict[Any, Any]) -> str:
    metric = result["metric"]
    name = metric["__name__"]

    if name == "cgroup_cpu_system_seconds":
        return "cpu/system-seconds"
    elif name == "cgroup_cpu_total_seconds":
        return "cpu/total-seconds"
    elif name == "cgroup_cpu_user_seconds":
        return "cpu/user-seconds"

    raise Exception(f"unsupported metric found {name}")


def get_node_name(result: dict[Any, Any]) -> str:
    metric = result["metric"]
    instance = metric["instance"]
    return instance.split(":")[0]


def get_job_id(result: dict[Any, Any]) -> str:
    metric = list(result.values())[0]["metric"]
    return metric["jobid"]


def get_metric_data(result: dict[Any, Any], metric_name: str):
    retyped_results = list(map(lambda x: (str(x[0]), float(x[1])), result["values"]))
    return pd.DataFrame(retyped_results, columns=["timestamp", metric_name])


def zipper_metric_data(
    a: dict[Any, Any], b: dict[Any, Any], c: dict[Any, Any]
) -> list[dict[Any, Any]]:
    output = []
    for key in a.keys():
        a_overall = a[key]
        b_overall = b[key]
        c_overall = c[key]

        a_name = get_metric_name(a_overall)
        b_name = get_metric_name(b_overall)
        c_name = get_metric_name(c_overall)

        # NOTE(@broarr): This is probably overkill for what I'm trying to do
        # TODO(@broarr): Remove the extra node checks from inside this loop
        a_node = get_node_name(a_overall)
        b_node = get_node_name(b_overall)
        c_node = get_node_name(c_overall)

        a_df = get_metric_data(a_overall, a_name)
        b_df = get_metric_data(b_overall, b_name)
        c_df = get_metric_data(c_overall, c_name)

        all_observations = a_df.merge(
            b_df, left_on="timestamp", right_on="timestamp", how="inner"
        ).merge(c_df, left_on="timestamp", right_on="timestamp", how="inner")
        all_observations["node"] = a_node
        all_observations["duration"] = int(STEP_SECONDS / 60)
        output.extend(all_observations.to_dict("records"))
    return output


def parse_responses(
    a: dict[Any, Any], b: dict[Any, Any], c: dict[Any, Any]
) -> dict[Any, Any]:
    a_overall = get_overall_metric(a)
    b_overall = get_overall_metric(b)
    c_overall = get_overall_metric(c)

    job_id = get_job_id(a_overall)

    inputs = zipper_metric_data(a_overall, b_overall, c_overall)

    return inputs


def system_cpu_seconds(jobid: str, start: datetime, end: datetime) -> dict[Any, Any]:
    query = f"cgroup_cpu_system_seconds{{cluster='oscar',jobid='{jobid}'}}"
    r = requests.get(
        QUERY_RANGE_ENDPOINT,
        {
            "query": query,
            "start": start,
            "end": end,
            "step": f"{STEP_SECONDS}s",
        },
    )
    return r.json()


def total_cpu_seconds(jobid: str, start: datetime, end: datetime) -> dict[Any, Any]:
    query = f"cgroup_cpu_total_seconds{{cluster='oscar',jobid='{jobid}'}}"
    r = requests.get(
        QUERY_RANGE_ENDPOINT,
        {
            "query": query,
            "start": start,
            "end": end,
            "step": f"{STEP_SECONDS}s",
        },
    )
    return r.json()


def user_cpu_seconds(jobid: str, start: datetime, end: datetime) -> dict[Any, Any]:
    query = f"cgroup_cpu_user_seconds{{cluster='oscar',jobid='{jobid}'}}"
    r = requests.get(
        QUERY_RANGE_ENDPOINT,
        {
            "query": query,
            "start": start,
            "end": end,
            "step": f"{STEP_SECONDS}s",
        },
    )
    return r.json()


def main():
    parser = ArgumentParser(
        description="generate observations for impact framework manifest"
    )
    parser.add_argument("jobid", help="job id to analyze")
    parser.add_argument("start", help="start time for job")
    parser.add_argument("end", help="end time for job")
    args = parser.parse_args()

    # TODO(@broarr): validate types here
    # NOTE(@broarr): We check to see if the job id is valid because we're using it
    #   directly in a prometheus query. There's no 'bind' like in a relational database
    #   handler. We need to be careful to prevent injection attacks

    cpu_total_resp = total_cpu_seconds(args.jobid, args.start, args.end)
    cpu_user_resp = user_cpu_seconds(args.jobid, args.start, args.end)
    cpu_system_resp = system_cpu_seconds(args.jobid, args.start, args.end)

    print(
        dump(
            {
                "tree": {
                    "children": {
                        f"job{job_id}": {
                            "inputs": parse_responses(
                                cpu_system_resp, cpu_total_resp, cpu_user_resp
                            )
                        }
                    }
                }
            }
        )
    )


if __name__ == "__main__":
    main()
