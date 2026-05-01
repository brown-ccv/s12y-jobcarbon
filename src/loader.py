from engine import LOOKBACK_DAYS, PrometheusEngine, Window
from models import NodeData
from registry import GPU_PROFILES, METRIC_REGISTRY, PROFILE_METRICS, NodeProfile


def _require_nonempty(result, message: str):
    if not result:
        raise ValueError(message)
    return result


def _prom_float(result) -> float:
    """Parse a Prometheus instant result value to float

    Prometheus encodes all values as strings on the wire, and integer metrics
    are sometimes represented as floats (e.g. "4.0"). This handles both cases
    """
    return float(result[0]["value"][1])


def _get_nodes(
    engine: PrometheusEngine, jobid: str, lookback_days: int
) -> tuple[list[str], Window]:
    """Finds all nodes a job ran on and the time window in which the job ran"""
    results = engine.query_lookback(
        METRIC_REGISTRY["job_cgroup"], jobid=jobid, lookback_days=lookback_days
    )
    if not results:
        raise ValueError(
            f"no cgroup data found for job {jobid} in the last {lookback_days} days"
        )
    nodes = sorted({r["metric"]["instance"].split(":")[0] for r in results})
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
    if dram_results:
        metrics["dram_power"] = dram_results
    if gpu_results:
        metrics["gpu_power"] = gpu_results
    for mid in PROFILE_METRICS[profile]:
        if mid not in metrics:
            metrics[mid] = engine.query_range(
                METRIC_REGISTRY[mid], window, node=node, jobid=jobid
            )

    cpu_total = _prom_float(
        _require_nonempty(
            engine.query_instant(
                METRIC_REGISTRY["node_cpu_total"], window.end, node=node
            ),
            f"no cpu capacity data for node {node}",
        )
    )
    mem_total = _prom_float(
        _require_nonempty(
            engine.query_instant(
                METRIC_REGISTRY["node_mem_total"], window.end, node=node
            ),
            f"no memory capacity data for node {node}",
        )
    )
    cpu_allocated = _prom_float(
        _require_nonempty(
            engine.query_instant(
                METRIC_REGISTRY["cgroup_cpus"], window.end, node=node, jobid=jobid
            ),
            f"no cpu allocation data for job {jobid} on node {node}",
        )
    )
    mem_allocated = _prom_float(
        _require_nonempty(
            engine.query_instant(
                METRIC_REGISTRY["cgroup_mem_total"], window.end, node=node, jobid=jobid
            ),
            f"no memory allocation data for job {jobid} on node {node}",
        )
    )

    gpu_count = 0
    if profile in GPU_PROFILES:
        gpu_count_result = engine.query_instant(
            METRIC_REGISTRY["gpu_count"], window.end, node=node, jobid=jobid
        )
        if gpu_count_result:
            gpu_count = _prom_float(gpu_count_result)

    return NodeData(
        node=node,
        profile=profile,
        metrics=metrics,
        cpu_total=int(cpu_total),
        mem_total=int(mem_total),
        cpu_allocated=int(cpu_allocated),
        mem_allocated=int(mem_allocated),
        gpu_count=int(gpu_count),
    )


def process_job(
    engine: PrometheusEngine, jobid: str, lookback_days: int = LOOKBACK_DAYS
) -> list[NodeData]:
    """Return one NodeData per node that ran the given Slurm job."""
    nodes, window = _get_nodes(engine, jobid, lookback_days)
    return [_process_node(engine, node, jobid, window) for node in nodes]
