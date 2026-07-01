from dataclasses import dataclass


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


