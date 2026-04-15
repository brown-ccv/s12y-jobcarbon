from engine import LOOKBACK_DAYS, PrometheusEngine, Window
from models import NodeData
from registry import METRIC_REGISTRY, PROFILE_METRICS, NodeProfile


def _get_nodes(
    engine: PrometheusEngine, jobid: str, lookback_days: int
) -> tuple[list[str], Window]:
    results = engine.query_lookback(
        METRIC_REGISTRY["job_cgroup"], jobid=jobid, lookback_days=lookback_days
    )
    if not results:
        raise ValueError(
            f"no cgroup data found for job {jobid} in the last {lookback_days} days"
        )
    nodes = list({r["metric"]["instance"].split(":")[0] for r in results})
    window = Window(
        start=int(min(v[0] for r in results for v in r["values"])),
        end=int(max(v[0] for r in results for v in r["values"])),
    )
    return nodes, window


def _process_node(
    engine: PrometheusEngine, node: str, jobid: str, window: Window
) -> NodeData:
    dram_results = engine.query_range(METRIC_REGISTRY["dram_power"], window, node=node)
    gpu_results = engine.query_range(
        METRIC_REGISTRY["gpu_power"], window, node=node, jobid=jobid
    )

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
        metrics[mid] = engine.query_range(
            METRIC_REGISTRY[mid], window, node=node, jobid=jobid
        )
    if dram_results:
        metrics["dram_power"] = dram_results
    if gpu_results:
        metrics["gpu_power"] = gpu_results

    cpu_result = engine.query_instant(
        METRIC_REGISTRY["node_cpu_total"], window.end, node=node
    )
    mem_result = engine.query_instant(
        METRIC_REGISTRY["node_mem_total"], window.end, node=node
    )
    cpu_alloc_result = engine.query_instant(
        METRIC_REGISTRY["cgroup_cpus"], window.end, node=node, jobid=jobid
    )
    mem_alloc_result = engine.query_instant(
        METRIC_REGISTRY["cgroup_mem_total"], window.end, node=node, jobid=jobid
    )

    if not cpu_result:
        raise ValueError(f"no cpu capacity data for node {node}")
    if not mem_result:
        raise ValueError(f"no memory capacity data for node {node}")
    if not cpu_alloc_result:
        raise ValueError(f"no cpu allocation data for job {jobid} on node {node}")
    if not mem_alloc_result:
        raise ValueError(f"no memory allocation data for job {jobid} on node {node}")

    # NOTE(@broarr): Sometimes prometheus encodes ints as floats. Everything is
    #   transmitted as a string; it's the wire format
    # TODO(@broarr): Will this int(float()) cast introduce inaccuracies?
    cpu_total = int(float(cpu_result[0]["value"][1]))
    mem_total = int(float(mem_result[0]["value"][1])) * 1024 * 1024
    cpu_allocated = int(float(cpu_alloc_result[0]["value"][1]))
    mem_allocated = int(float(mem_alloc_result[0]["value"][1]))

    return NodeData(
        node=node,
        profile=profile,
        metrics=metrics,
        cpu_total=cpu_total,
        mem_total=mem_total,
        cpu_allocated=cpu_allocated,
        mem_allocated=mem_allocated,
    )


def process_job(
    engine: PrometheusEngine, jobid: str, lookback_days: int = LOOKBACK_DAYS
) -> list[NodeData]:
    nodes, window = _get_nodes(engine, jobid, lookback_days)
    return [_process_node(engine, node, jobid, window) for node in nodes]
