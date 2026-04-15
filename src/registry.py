from dataclasses import dataclass
from enum import Enum


@dataclass
class MetricDefinition:
    id: str
    query: str  # PromQL template string, parameters: {node}, {jobid}
    unit: str


# Nodes without Scaphandre data are skipped — no estimation fallback.
METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "cpu_power": MetricDefinition(
        id="cpu_power",
        query="sum by (instance) (scaph_socket_power_microwatts{{instance=~'{node}:.*'}})",
        unit="microwatts",
    ),
    "dram_power": MetricDefinition(
        id="dram_power",
        query="sum by (instance) (scaph_domain_power_microwatts{{domain_name='dram',instance=~'{node}:.*'}})",
        unit="microwatts",
    ),
    "host_power": MetricDefinition(
        id="host_power",
        query="scaph_host_power_microwatts{{instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "gpu_power": MetricDefinition(
        id="gpu_power",
        # Multiply by 1000 to convert milliwatts → microwatts, consistent with all other power metrics.
        query="sum by (instance) (nvidia_gpu_power_usage_milliwatts{{instance=~'{node}:.*',jobid='{jobid}'}} * 1000)",
        unit="microwatts",
    ),
    "node_cpu_total": MetricDefinition(
        id="node_cpu_total",
        query="slurm_node_cpu_total{{node='{node}'}}",
        unit="cores",
    ),
    "node_mem_total": MetricDefinition(
        id="node_mem_total",
        query="slurm_node_mem_total{{node='{node}'}}",
        unit="megabytes",
    ),
    # engine.query() only — no instance filter, bakes step/task filters in to return
    # one series per node. Used to discover nodes and derive the job window.
    "job_cgroup": MetricDefinition(
        id="job_cgroup",
        query="cgroup_cpu_total_seconds{{jobid='{jobid}',step='',task=''}}",
        unit="seconds",
    ),
    "cgroup_window": MetricDefinition(
        id="cgroup_window",
        query="cgroup_cpu_total_seconds{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="seconds",
    ),
    # step='',task='' filters to the job-level cgroup row, excluding sub-cgroup steps/tasks.
    "cgroup_cpus": MetricDefinition(
        id="cgroup_cpus",
        query="cgroup_cpus{{instance=~'{node}:.*',jobid='{jobid}',step='',task=''}}",
        unit="cores",
    ),
    "cgroup_mem_total": MetricDefinition(
        id="cgroup_mem_total",
        query="cgroup_memory_total_bytes{{instance=~'{node}:.*',jobid='{jobid}',step='',task=''}}",
        unit="bytes",
    ),
}


class NodeProfile(Enum):
    FULL          = "full"
    FULL_GPU      = "full_gpu"
    HOST_ONLY     = "host_only"
    HOST_ONLY_GPU = "host_only_gpu"


# node_cpu_total and node_mem_total are fetched separately and stored as scalars on NodeData.
PROFILE_METRICS: dict[NodeProfile, list[str]] = {
    NodeProfile.FULL:          ["cpu_power", "dram_power"],
    NodeProfile.FULL_GPU:      ["cpu_power", "dram_power", "gpu_power"],
    NodeProfile.HOST_ONLY:     ["host_power"],
    NodeProfile.HOST_ONLY_GPU: ["host_power", "gpu_power"],
}
