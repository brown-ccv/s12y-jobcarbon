import dataclasses
import logging
from pathlib import Path

import yaml

from jobconfig import Config, PROCESS_SCALARS, MEM_SCALARS
from models import NodeData
from registry import GPU_PROFILES, NodeProfile
from synthesis import synthesize

logger = logging.getLogger(__name__)


def _get_plugin_dir() -> Path:
    """Return the path to the plugins directory."""
    return Path(__file__).parent / "plugins"


def _load_plugin(name: str) -> dict:
    """Load and parse a single plugin YAML file by step name."""
    with (_get_plugin_dir() / f"{name}.yaml").open() as f:
        return yaml.safe_load(f)


_OPERATIONAL_STEPS = {
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

_EMBODIED_STEPS_SERVER_ONLY = [
    "server-embodied",
    "sum-embodied",
    "sum-carbon",
]

_EMBODIED_STEPS_GPU_PCF = [
    "server-embodied",
    "gpu-embodied-pcf",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

_EMBODIED_STEPS_GPU_ESTIMATED = [
    "server-embodied",
    "gpu-chip-embodied",
    "gpu-vram-embodied",
    "gpu-embodied-per-gpu",
    "gpu-embodied-total",
    "gpu-embodied-per-second",
    "gpu-embodied-time-scale",
    "sum-embodied-gpu",
    "sum-carbon",
]

_AGGREGATION_OPERATIONAL = {
    "metrics": ["duration", "power", "carbon_operational"],
    "type": "both",
}

_AGGREGATION_EMBODIED = {
    "metrics": ["duration", "power", "carbon_operational", "carbon_embodied", "carbon"],
    "type": "both",
}


def _embodied_steps(node_data: NodeData, config: Config) -> list[str]:
    """Return the ordered embodied pipeline steps for this node's profile."""
    if node_data.profile not in GPU_PROFILES:
        return _EMBODIED_STEPS_SERVER_ONLY
    entry = config.gpu_for_node(node_data.node)
    if entry is None:
        raise ValueError(
            f"node '{node_data.node}' has a GPU profile but is not in gpu_config; "
            "re-run create-config and add it to jobcarbon.toml"
        )
    if "pcf_gco2eq" in entry:
        return _EMBODIED_STEPS_GPU_PCF
    return _EMBODIED_STEPS_GPU_ESTIMATED


def _pipeline_steps(node_data: NodeData, config: Config) -> list[str]:
    """Return the full ordered pipeline step list for this node."""
    steps = list(_OPERATIONAL_STEPS[node_data.profile])
    if config.embodied:
        steps.extend(_embodied_steps(node_data, config))
    return steps


def _gpu_defaults(node_data: NodeData, config: Config) -> dict:
    """Return embodied GPU defaults to inject into the node defaults block."""
    if node_data.profile not in GPU_PROFILES:
        return {}
    entry = config.gpu_for_node(node_data.node)
    assert entry is not None  # already verified by _embodied_steps
    if "pcf_gco2eq" in entry:
        return {
            "gpu_count": node_data.gpu_count,
            "pcf_carbon_per_gpu": entry["pcf_gco2eq"],
        }
    process = entry["process"]
    mem_type = entry["mem_type"]
    if process not in PROCESS_SCALARS:
        raise ValueError(
            f"unknown process {process!r} — must be one of: {', '.join(sorted(PROCESS_SCALARS))}"
        )
    if mem_type not in MEM_SCALARS:
        raise ValueError(
            f"unknown mem_type {mem_type!r} — must be one of: {', '.join(sorted(MEM_SCALARS))}"
        )
    if process == "samsung-8n":
        logger.warning(
            "GPU '%s': samsung-8n is not in Boakes et al.; using TSMC N7 scalar as proxy.",
            entry.get("gpu_model", "unknown"),
        )
    return {
        "gpu_count": node_data.gpu_count,
        "die_area_sq_cm": entry["die_area_cm2"],
        "vram_gb": entry["vram_gb"],
        "process_scalar_carbon_per_sq_cm": PROCESS_SCALARS[process],
        "mem_scalar_carbon_per_gb": MEM_SCALARS[mem_type],
    }


def _node_defaults(node_data: NodeData, config: Config) -> dict:
    """Build the defaults block for a single node, gating embodied fields on config."""
    defaults = {"grid_carbon_intensity": config.grid_carbon_intensity}
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


def _build_node(node_data: NodeData, config: Config) -> dict:
    """Assemble the pipeline, defaults, and inputs entry for a single node."""
    steps = _pipeline_steps(node_data, config)
    observations = synthesize(node_data, config.step_seconds)
    return {
        "pipeline": {"compute": steps},
        "defaults": _node_defaults(node_data, config),
        "inputs": [
            {k: v for k, v in dataclasses.asdict(obs).items() if v is not None}
            for obs in observations
        ],
    }


def generate_manifest(jobid: str, node_data: list[NodeData], config: Config) -> dict:
    """Build one IF manifest for an entire job, with one tree child per node."""
    all_plugins = {}
    for nd in node_data:
        for step in _pipeline_steps(nd, config):
            if step not in all_plugins:
                all_plugins[step] = _load_plugin(step)

    aggregation = _AGGREGATION_EMBODIED if config.embodied else _AGGREGATION_OPERATIONAL

    return {
        "name": f"job{jobid}",
        "description": f"Carbon estimate for job {jobid}",
        "aggregation": aggregation,
        "initialize": {"plugins": all_plugins},
        "tree": {"children": {nd.node: _build_node(nd, config) for nd in node_data}},
    }
