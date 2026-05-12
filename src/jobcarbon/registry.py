from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    query: str  # PromQL template string, parameters: {node}, {jobid}


# Nodes without Scaphandre data are skipped — no estimation fallback
# All power metrics are in kilowatts
# All memory metrics are in GiB
# TODO(@broarr): consider using a cpu_utilization metric instead of the static allocated and total
METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "cpu_power": MetricDefinition(
        id="cpu_power",
        query="avg_over_time((sum by (instance) (scaph_socket_power_microwatts{{instance=~'{node}:.*'}}) / 1e9)[{step}s:])",
    ),
    "dram_power": MetricDefinition(
        id="dram_power",
        query="avg_over_time((sum by (instance) (scaph_domain_power_microwatts{{domain_name='dram',instance=~'{node}:.*'}}) / 1e9)[{step}s:])",
    ),
    "host_power": MetricDefinition(
        id="host_power",
        query="avg_over_time((scaph_host_power_microwatts{{instance=~'{node}:.*'}} / 1e9)[{step}s:])",
    ),
    "gpu_power": MetricDefinition(
        id="gpu_power",
        query="avg_over_time((sum by (instance) (nvidia_gpu_power_usage_milliwatts{{instance=~'{node}:.*',jobid='{jobid}'}} / 1e6))[{step}s:])",
    ),
    "node_cpu_total": MetricDefinition(
        id="node_cpu_total",
        query="slurm_node_cpu_total{{node='{node}'}}",
    ),
    "node_mem_total": MetricDefinition(
        id="node_mem_total",
        query="slurm_node_mem_total{{node='{node}'}} / 1024",
    ),
    "cgroup_window": MetricDefinition(
        id="cgroup_window",
        query="cgroup_cpu_total_seconds{{instance=~'{node}:.*',jobid='{jobid}'}}",
    ),
    # step='',task='' filters to the job-level cgroup row, excluding sub-cgroup steps/tasks
    "job_cgroup": MetricDefinition(
        id="job_cgroup",
        query="cgroup_cpu_total_seconds{{jobid='{jobid}',step='',task=''}}",
    ),
    "cgroup_cpus": MetricDefinition(
        id="cgroup_cpus",
        query="cgroup_cpus{{instance=~'{node}:.*',jobid='{jobid}',step='',task=''}}",
    ),
    "cgroup_mem_total": MetricDefinition(
        id="cgroup_mem_total",
        query="cgroup_memory_total_bytes{{instance=~'{node}:.*',jobid='{jobid}',step='',task=''}} / 1024 / 1024 / 1024",
    ),
    "gpu_count": MetricDefinition(
        id="gpu_count",
        query="count(count by (minor_number) (nvidia_gpu_power_usage_milliwatts{{instance=~'{node}:.*',jobid='{jobid}'}}))",
    ),
}


class NodeProfile(Enum):
    FULL = "full"
    FULL_GPU = "full_gpu"
    HOST_ONLY = "host_only"
    HOST_ONLY_GPU = "host_only_gpu"


PROFILE_METRICS: dict[NodeProfile, list[str]] = {
    NodeProfile.FULL: ["cpu_power", "dram_power"],
    NodeProfile.FULL_GPU: ["cpu_power", "dram_power", "gpu_power"],
    NodeProfile.HOST_ONLY: ["host_power"],
    NodeProfile.HOST_ONLY_GPU: ["host_power", "gpu_power"],
}

GPU_PROFILES = {NodeProfile.FULL_GPU, NodeProfile.HOST_ONLY_GPU}
HOST_PROFILES = {NodeProfile.HOST_ONLY, NodeProfile.HOST_ONLY_GPU}
