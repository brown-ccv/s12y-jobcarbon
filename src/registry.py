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
        query="scaph_domain_power_microwatts{{domain_name='core',instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "dram_power": MetricDefinition(
        id="dram_power",
        query="scaph_domain_power_microwatts{{domain_name='dram',instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "host_power": MetricDefinition(
        id="host_power",
        query="scaph_host_power_microwatts{{instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "gpu_power": MetricDefinition(
        id="gpu_power",
        query="nvidia_gpu_power_usage_milliwatts{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="milliwatts",
    ),
    "node_cpu_cap": MetricDefinition(
        id="node_cpu_cap",
        query="slurm_node_cpu_total{{node='{node}'}}",
        unit="cores",
    ),
    "node_mem_cap": MetricDefinition(
        id="node_mem_cap",
        query="slurm_node_mem_total{{node='{node}'}}",
        unit="megabytes",
    ),
    # Queried without a node filter to discover all nodes for a job and derive
    # the time window. Node names and timestamps are extracted from top-level
    # rows only (no step, no task), but all rows are forwarded to the zipper.
    "cgroup_window": MetricDefinition(
        id="cgroup_window",
        query="cgroup_cpu_total_seconds{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="seconds",
    ),
}


class NodeProfile(Enum):
    FULL      = "full"
    HOST_ONLY = "host_only"


# gpu_power is in both profiles — an empty result means no GPUs on this job/node.
# dram_power is fetched during discovery and reused for FULL, not re-fetched.
PROFILE_METRICS: dict[NodeProfile, list[str]] = {
    NodeProfile.FULL:      ["cpu_power", "dram_power", "gpu_power", "node_cpu_cap", "node_mem_cap"],
    NodeProfile.HOST_ONLY: ["host_power", "gpu_power", "node_cpu_cap", "node_mem_cap"],
}
