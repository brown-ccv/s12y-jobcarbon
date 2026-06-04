import dataclasses
import logging
from typing import Any, cast

import yaml
from importlib import resources

from .config import Config, EstimatedGpuSpec, is_pcf_spec
from .models import NodeData
from .registry import GPU_PROFILES, NodeProfile
from .alignment import align

type Manifest = dict[str, Any]

logger = logging.getLogger(__name__)

OPERATIONAL_STEPS = {
    NodeProfile.FULL: [
        "sum-scaph-power",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ],
    NodeProfile.FULL_GPU: [
        "sum-scaph-gpu-power",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ],
    NodeProfile.HOST_ONLY: [
        "cpu-share",
        "mem-share",
        "weight-cpu-share",
        "weight-mem-share",
        "reservation-share",
        "scale-host-power",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ],
    NodeProfile.HOST_ONLY_GPU: [
        "cpu-share",
        "mem-share",
        "weight-cpu-share",
        "weight-mem-share",
        "reservation-share",
        "scale-host-power-gpu",
        "sum-node-gpu-power",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ],
}

EMBODIED_STEPS_SERVER_ONLY = [
    "server-embodied",
    "sum-embodied",
    "sum-carbon",
]

EMBODIED_STEPS_GPU_PCF = [
    "server-embodied",
    "gpu-embodied-pcf",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

EMBODIED_STEPS_GPU_ESTIMATED = [
    "server-embodied",
    "gpu-chip-embodied",
    "gpu-chip-yield-correct",
    "gpu-vram-embodied",
    "gpu-embodied-per-gpu",
    "gpu-embodied-total",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

OPERATIONAL_DEFAULTS = {
    NodeProfile.FULL: [],
    NodeProfile.FULL_GPU: [],
    NodeProfile.HOST_ONLY: ["cpu_total", "mem_total", "cpu_allocated", "mem_allocated"],
    NodeProfile.HOST_ONLY_GPU: [
        "cpu_total",
        "mem_total",
        "cpu_allocated",
        "mem_allocated",
    ],
}

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
    if node_data.profile not in GPU_PROFILES:
        return EMBODIED_STEPS_SERVER_ONLY
    entry = config.gpu_for_node(node_data.node)
    if entry is None:
        raise ValueError(
            f"node '{node_data.node}' has a GPU profile but is not in gpu_config"
        )
    if is_pcf_spec(entry):
        return EMBODIED_STEPS_GPU_PCF
    return EMBODIED_STEPS_GPU_ESTIMATED


def _pipeline_steps(node_data: NodeData, config: Config) -> list[str]:
    """Return the full ordered pipeline step list for this node."""
    steps = list(OPERATIONAL_STEPS[node_data.profile])
    if config.embodied:
        steps.extend(_embodied_steps(node_data, config))
    return steps


def _gpu_defaults(node_data: NodeData, config: Config) -> Manifest:
    """Return embodied GPU defaults to inject into the node defaults block."""
    if node_data.profile not in GPU_PROFILES:
        return {}
    entry = config.gpu_for_node(node_data.node)
    if entry is None:
        raise ValueError(
            f"node '{node_data.node}' has a GPU profile but is not in gpu_config"
        )

    if is_pcf_spec(entry):
        return {
            "gpu_count": node_data.gpu_count,
            "pcf_carbon_per_gpu": entry["pcf_carbon_per_gpu"],
        }

    estimated = cast(EstimatedGpuSpec, entry)
    process = estimated.get("process")
    mem_type = estimated.get("mem_type")
    if process not in config.process_scalars:
        raise ValueError(
            f"unknown process {process!r} — must be one of: {', '.join(sorted(config.process_scalars))}"
        )
    if mem_type not in config.mem_scalars:
        raise ValueError(
            f"unknown mem_type {mem_type!r} — must be one of: {', '.join(sorted(config.mem_scalars))}"
        )
    if process == "samsung-8n":
        logger.warning(
            "GPU '%s': samsung-8n is not in Boakes et al.; using TSMC N7 scalar as proxy.",
            estimated.get("gpu_model", "unknown"),
        )

    return {
        "gpu_count": node_data.gpu_count,
        "die_area_sq_cm": estimated["die_area_sq_cm"],
        "vram_gb": estimated["vram_gb"],
        "process_scalar_carbon_per_sq_cm": config.process_scalars[process],
        "mem_scalar_carbon_per_gb": config.mem_scalars[mem_type],
        "yield_factor": config.yield_factor,
    }


def _node_defaults(node_data: NodeData, config: Config) -> Manifest:
    """Build the defaults block for a single node, gating embodied fields on
    config."""
    defaults: dict[str, Any] = {}
    if "grid_carbon_intensity" not in node_data.metrics:
        defaults["grid_carbon_intensity"] = config.grid_carbon_intensity
    for field in OPERATIONAL_DEFAULTS[node_data.profile]:
        defaults[field] = getattr(node_data, field)
    if config.embodied:
        defaults.update(
            {
                "cpu_lifespan_seconds": config.cpu_lifespan_seconds,
                "gpu_lifespan_seconds": config.gpu_lifespan_seconds,
                "cpu_total": node_data.cpu_total,
                "mem_total": node_data.mem_total,
                "cpu_allocated": node_data.cpu_allocated,
                "mem_allocated": node_data.mem_allocated,
                **_gpu_defaults(node_data, config),
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
