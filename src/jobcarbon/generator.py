import dataclasses
import logging
from typing import Any

import yaml
from importlib import resources

from .config import (
    Config,
    gpu_direct_carbon,
)
from .embodied import cpu_embodied_inputs, gpu_embodied_inputs
from .models import NodeData
from .alignment import align

type Manifest = dict[str, Any]

logger = logging.getLogger(__name__)

OPERATIONAL_STEPS_CPU = [
    "cpu-share",
    "scale-cpu-power",
    "sum-attributed-power",
    "duration-to-hours",
    "calculate-energy",
    "calculate-carbon-operational",
]

OPERATIONAL_STEPS_CPU_GPU = [
    "cpu-share",
    "scale-cpu-power",
    "sum-attributed-power-gpu",
    "duration-to-hours",
    "calculate-energy",
    "calculate-carbon-operational",
]

OPERATIONAL_STEPS_CPU_DRAM = [
    "cpu-share",
    "scale-cpu-power",
    "mem-share",
    "scale-dram-power",
    "sum-attributed-power-dram",
    "duration-to-hours",
    "calculate-energy",
    "calculate-carbon-operational",
]

OPERATIONAL_STEPS_CPU_DRAM_GPU = [
    "cpu-share",
    "scale-cpu-power",
    "mem-share",
    "scale-dram-power",
    "sum-attributed-power-dram-gpu",
    "duration-to-hours",
    "calculate-energy",
    "calculate-carbon-operational",
]

# CPU + DRAM embodied carbon (BoaviztAPI bottom-up); replaces SciEmbodied.
EMBODIED_STEPS_CPU_DRAM = [
    "cpu-embodied-share",
    "mem-embodied-share",
    "cpu-die-embodied",
    "cpu-embodied-per-socket",
    "cpu-embodied-node",
    "cpu-embodied-attributed",
    "cpu-embodied-per-second",
    "cpu-embodied-time-scale",
    "dram-die-area",
    "dram-die-embodied",
    "dram-embodied-node",
    "dram-embodied-attributed",
    "dram-embodied-per-second",
    "dram-embodied-time-scale",
]

EMBODIED_STEPS_SERVER_ONLY = [
    *EMBODIED_STEPS_CPU_DRAM,
    "sum-embodied",
    "sum-carbon",
]

EMBODIED_STEPS_GPU_DIRECT = [
    *EMBODIED_STEPS_CPU_DRAM,
    "gpu-embodied-direct",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

EMBODIED_STEPS_GPU_ESTIMATED = [
    *EMBODIED_STEPS_CPU_DRAM,
    "gpu-chip-embodied",
    "gpu-vram-embodied",
    "gpu-embodied-per-gpu",
    "gpu-embodied-total",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

AGGREGATION_OPERATIONAL = {
    "metrics": ["duration", "energy", "carbon_operational"],
    "type": "both",
}

AGGREGATION_EMBODIED = {
    "metrics": [
        "duration",
        "energy",
        "carbon_operational",
        "carbon_embodied",
        "carbon",
    ],
    "type": "both",
}


def _load_plugin(name: str) -> Manifest:
    """Load and parse a single plugin YAML file from package resources."""
    resource = resources.files("jobcarbon").joinpath("plugins").joinpath(f"{name}.yaml")
    # resources.as_file ensures we have a real file system Path to read from.
    with resources.as_file(resource) as path:
        text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _embodied_steps(node_data: NodeData, config: Config) -> list[str]:
    """Return the ordered embodied pipeline steps for this node's profile."""
    if not node_data.gpu_count:
        return EMBODIED_STEPS_SERVER_ONLY
    entry = config.gpu_for_node(node_data.node)
    if entry is None:
        raise ValueError(
            f"node '{node_data.node}' has a GPU profile but is not in gpu_config"
        )
    if gpu_direct_carbon(entry) is not None:
        return EMBODIED_STEPS_GPU_DIRECT
    return EMBODIED_STEPS_GPU_ESTIMATED


def _operational_steps(node_data: NodeData) -> list[str]:
    """Return the ordered operational pipeline steps for this node."""
    has_dram = "dram_power" in node_data.metrics
    has_gpu = node_data.gpu_count > 0
    if has_dram and has_gpu:
        return OPERATIONAL_STEPS_CPU_DRAM_GPU
    if has_dram:
        return OPERATIONAL_STEPS_CPU_DRAM
    if has_gpu:
        return OPERATIONAL_STEPS_CPU_GPU
    return OPERATIONAL_STEPS_CPU


def _pipeline_steps(node_data: NodeData, config: Config) -> list[str]:
    """Return the full ordered pipeline step list for this node."""
    steps = list(_operational_steps(node_data))
    if config.embodied:
        steps.extend(_embodied_steps(node_data, config))
    return steps


def _node_defaults(node_data: NodeData, config: Config) -> Manifest:
    """Build the defaults block for a single node, gating embodied fields on
    config."""
    defaults: dict[str, Any] = {}
    if "grid_carbon_intensity" not in node_data.metrics:
        defaults["grid_carbon_intensity"] = config.grid_carbon_intensity
    defaults["cpu_total"] = node_data.cpu_total
    defaults["cpu_allocated"] = node_data.cpu_allocated
    defaults["mem_total"] = node_data.mem_total
    defaults["mem_allocated"] = node_data.mem_allocated
    if config.embodied:
        defaults.update(
            {
                "cpu_lifespan_seconds": config.cpu_lifespan_seconds,
                "gpu_lifespan_seconds": config.gpu_lifespan_seconds,
                **cpu_embodied_inputs(node_data.node, node_data.socket_count, config),
                **gpu_embodied_inputs(node_data.node, node_data.gpu_count, config),
            }
        )
    return defaults


def _build_node(node_data: NodeData, config: Config) -> Manifest:
    """Assemble the pipeline, defaults, and inputs entry for a single node."""
    steps = _pipeline_steps(node_data, config)
    observations = align(node_data, config.step_seconds)
    return {
        "pipeline": {"compute": steps},
        "defaults": _node_defaults(node_data, config),
        "inputs": [
            {k: v for k, v in dataclasses.asdict(obs).items() if v is not None}
            for obs in observations
        ],
    }


def generate_manifest(
    jobid: str, node_data: list[NodeData], config: Config
) -> Manifest:
    """Build one IF manifest for an entire job, with one tree child per
    node."""
    all_plugins: dict[str, Manifest] = {}
    for nd in node_data:
        for step in _pipeline_steps(nd, config):
            if step not in all_plugins:
                all_plugins[step] = _load_plugin(step)

    aggregation = AGGREGATION_EMBODIED if config.embodied else AGGREGATION_OPERATIONAL

    return {
        "name": f"job{jobid}",
        "description": f"Carbon estimate for job {jobid}",
        "aggregation": aggregation,
        "initialize": {"plugins": all_plugins},
        "tree": {"children": {nd.node: _build_node(nd, config) for nd in node_data}},
    }
