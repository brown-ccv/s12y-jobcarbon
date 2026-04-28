import dataclasses
import logging
from pathlib import Path

import yaml

from config import Config, PROCESS_SCALARS, MEM_SCALARS
from models import NodeData
from registry import GPU_PROFILES
from synthesis import synthesize

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _template_name(node_data: NodeData, config: Config) -> str:
    if node_data.profile not in GPU_PROFILES:
        return node_data.profile.value
    entry = config.gpu_for_node(node_data.node)
    if entry is None:
        raise ValueError(
            f"node '{node_data.node}' has a GPU profile but is not in gpu_config; "
            "re-run create-config and add it to jobcarbon.toml"
        )
    suffix = "_pcf" if "pcf_gco2eq" in entry else "_estimated"
    return f"{node_data.profile.value}{suffix}"


def _load_template(node_data: NodeData, gpu_config: dict) -> dict:
    name = _template_name(node_data, gpu_config)
    with (TEMPLATES_DIR / f"{name}.yaml").open() as f:
        return yaml.safe_load(f)


def _gpu_defaults(node_data: NodeData, config: Config) -> dict:
    if node_data.profile not in GPU_PROFILES:
        return {}
    entry = config.gpu_for_node(node_data.node)
    assert entry is not None  # already verified by _template_name
    if "pcf_gco2eq" in entry:
        return {
            "gpu_count": node_data.gpu_count,
            "pcf_gco2eq": float(entry["pcf_gco2eq"]),
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
        "die_area_cm2": float(entry["die_area_cm2"]),
        "vram_gb": float(entry["vram_gb"]),
        "process_scalar_gco2eq_per_cm2": PROCESS_SCALARS[process],
        "mem_scalar_gco2eq_per_gb": MEM_SCALARS[mem_type],
    }


def _build_node(
    node_data: NodeData,
    template: dict,
    config: Config,
) -> dict:
    observations = synthesize(node_data.node, node_data.metrics)
    return {
        "pipeline": template["pipeline"],
        "defaults": {
            "grid_carbon_intensity": config.grid_carbon_intensity,
            "cpu_total": node_data.cpu_total,
            "mem_total": node_data.mem_total,
            "cpu_allocated": node_data.cpu_allocated,
            "mem_allocated": node_data.mem_allocated,
            **_gpu_defaults(node_data, config),
        },
        "inputs": [dataclasses.asdict(obs) for obs in observations],
    }


def generate_manifest(
    jobid: str,
    node_data: list[NodeData],
    config: Config,
) -> dict:
    """Build one IF manifest for an entire job, with one tree child per node.

    The initialize block is the union of plugins from all node profiles present.
    Each child declares its own pipeline list drawn from its profile template.
    """
    templates = {nd.node: _load_template(nd, config) for nd in node_data}

    all_plugins = {}
    for tmpl in templates.values():
        for name, defn in tmpl["initialize"]["plugins"].items():
            all_plugins[name] = defn

    aggregation = next(iter(templates.values()))["aggregation"]

    return {
        "name": f"job{jobid}",
        "description": f"Carbon estimate for job {jobid}",
        "aggregation": aggregation,
        "initialize": {"plugins": all_plugins},
        "tree": {
            "children": {
                nd.node: _build_node(nd, templates[nd.node], config) for nd in node_data
            }
        },
    }
