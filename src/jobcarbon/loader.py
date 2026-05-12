from .engine import PrometheusEngine, Window
from .models import NodeData
from .registry import (
    GPU_PROFILES,
    HOST_PROFILES,
    METRIC_REGISTRY,
    MetricDefinition,
    NodeProfile,
)


def _require_nonempty[T](result: T, message: str) -> T:
    if not result:
        raise ValueError(message)
    return result


def _prom_int(result: list[dict]) -> int:
    """Parse a Prometheus instant result value to int.

    Prometheus encodes all values as strings on the wire, and integer
    metrics are sometimes represented as floats (e.g. "4.0"). This
    handles both cases
    """
    return int(float(result[0]["value"][1]))


def _query_instant(
    engine: PrometheusEngine,
    metric: MetricDefinition,
    timestamp: int,
    *,
    message: str | None = None,
    error: bool = True,
    **query_kwargs,
) -> int:
    """Query a Prometheus instant metric, parse and return it."""
    result = engine.query_instant(metric, timestamp, **query_kwargs)
    if not result and error:
        if message is None:
            message = f"no {metric} data"
        raise ValueError(message)
    return _prom_int(result)


def _get_nodes(
    engine: PrometheusEngine, jobid: str, lookback_days: int
) -> tuple[list[str], Window]:
    """Finds all nodes a job ran on and the time window in which the job
    ran."""
    # TODO(@broarr): Check if job is running via Slurm prometheus exporter
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
    """Pulls the metrics for each node for a given window."""
    cpu_results = engine.query_range(METRIC_REGISTRY["cpu_power"], window, node=node)
    dram_results = engine.query_range(METRIC_REGISTRY["dram_power"], window, node=node)
    gpu_results = engine.query_range(
        METRIC_REGISTRY["gpu_power"], window, node=node, jobid=jobid
    )

    if cpu_results and dram_results and gpu_results:
        profile = NodeProfile.FULL_GPU
    elif cpu_results and dram_results:
        profile = NodeProfile.FULL
    elif gpu_results:
        profile = NodeProfile.HOST_ONLY_GPU
    else:
        profile = NodeProfile.HOST_ONLY

    metrics = {}
    if cpu_results:
        metrics["cpu_power"] = cpu_results
    if dram_results:
        metrics["dram_power"] = dram_results
    if gpu_results:
        metrics["gpu_power"] = gpu_results
    if profile in HOST_PROFILES:
        metrics["host_power"] = engine.query_range(
            METRIC_REGISTRY["host_power"], window, node=node, jobid=jobid
        )

    cpu_total = _query_instant(
        engine,
        METRIC_REGISTRY["node_cpu_total"],
        window.end,
        node=node,
        message=f"no cpu capacity data for node {node}",
    )

    mem_total = _query_instant(
        engine,
        METRIC_REGISTRY["node_mem_total"],
        window.end,
        node=node,
        message=f"no memory capacity data for node {node}",
    )

    cpu_allocated = _query_instant(
        engine,
        METRIC_REGISTRY["cgroup_cpus"],
        window.end,
        node=node,
        jobid=jobid,
        message=f"no cpu allocation data for job {jobid} on node {node}",
    )

    mem_allocated = _query_instant(
        engine,
        METRIC_REGISTRY["cgroup_mem_total"],
        window.end,
        node=node,
        jobid=jobid,
        message=f"no memory allocation data for job {jobid} on node {node}",
    )

    gpu_count = 0
    if profile in GPU_PROFILES:
        gpu_count = _query_instant(
            engine,
            METRIC_REGISTRY["gpu_count"],
            window.end,
            error=False,
            node=node,
            jobid=jobid,
        )

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
    engine: PrometheusEngine, jobid: str, lookback_days: int
) -> list[NodeData]:
    """Return one NodeData per node that ran the given Slurm job."""
    nodes, window = _get_nodes(engine, jobid, lookback_days)
    return [_process_node(engine, node, jobid, window) for node in nodes]
