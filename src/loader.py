from engine import LOOKBACK_DAYS, PrometheusEngine, Window
from models import NodeData
from registry import METRIC_REGISTRY, PROFILE_METRICS, NodeProfile


def _get_nodes(engine: PrometheusEngine, jobid: str, lookback_days: int) -> tuple[list[str], Window]:
    results = engine.query(METRIC_REGISTRY["job_cgroup"], jobid=jobid, lookback_days=lookback_days)
    if not results:
        raise ValueError(f"no cgroup data found for job {jobid} in the last {lookback_days} days")
    nodes = list({r["metric"]["instance"].split(":")[0] for r in results})
    window = Window(
        start=int(min(v[0] for r in results for v in r["values"])),
        end=int(max(v[0] for r in results for v in r["values"])),
    )
    return nodes, window


def _process_node(engine: PrometheusEngine, node: str, jobid: str, window: Window) -> NodeData:
    dram_results = engine.query_range(METRIC_REGISTRY["dram_power"], window, node=node)
    gpu_results  = engine.query_range(METRIC_REGISTRY["gpu_power"],  window, node=node, jobid=jobid)

    if dram_results and gpu_results:
        profile = NodeProfile.FULL_GPU
    elif dram_results and not gpu_results:
        profile = NodeProfile.FULL
    elif not dram_results and gpu_results:
        profile = NodeProfile.HOST_ONLY_GPU
    else:
        profile = NodeProfile.HOST_ONLY

    metrics = {}
    for mid in PROFILE_METRICS[profile]:
        metrics[mid] = engine.query_range(METRIC_REGISTRY[mid], window, node=node, jobid=jobid)
    if dram_results:
        metrics["dram_power"] = dram_results
    if gpu_results:
        metrics["gpu_power"] = gpu_results

    cpu_series = engine.query_range(METRIC_REGISTRY["node_cpu_total"], window, node=node)
    mem_series = engine.query_range(METRIC_REGISTRY["node_mem_total"], window, node=node)

    if not cpu_series:
        raise ValueError(f"no cpu capacity data for node {node}")
    if not mem_series:
        raise ValueError(f"no memory capacity data for node {node}")

    # Prometheus returns all sample values as strings and may encode integers as floats
    # (e.g. "8.000000e+00"), so int() alone would fail. float() normalises first
    cpu_total = int(float(cpu_series[0]["values"][0][1]))
    mem_total = int(float(mem_series[0]["values"][0][1]))

    return NodeData(node=node, profile=profile, metrics=metrics, cpu_total=cpu_total, mem_total=mem_total)


def process_job(engine: PrometheusEngine, jobid: str, lookback_days: int = LOOKBACK_DAYS) -> list[NodeData]:
    nodes, window = _get_nodes(engine, jobid, lookback_days)
    return [_process_node(engine, node, jobid, window) for node in nodes]
