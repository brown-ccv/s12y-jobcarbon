import dataclasses
from pathlib import Path

import yaml

from models import NodeData
from synthesis import synthesize

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _load_template(node_data: NodeData) -> dict:
    template_path = TEMPLATES_DIR / f"{node_data.profile.value}.yaml"
    with template_path.open() as f:
        return yaml.safe_load(f)


def generate_manifest(
    jobid: str,
    node_data_list: list[NodeData],
    grid_carbon_intensity: float,
) -> dict:
    """Build one IMP manifest for an entire job, with one tree child per node.

    The initialize block is the union of plugins from all node profiles present.
    Each child declares its own pipeline list drawn from its profile template.
    """
    templates = {nd.node: _load_template(nd) for nd in node_data_list}

    # Union of all plugins across profiles — later profiles do not overwrite earlier
    # ones with the same key, since identical plugin names across profiles have identical
    # definitions (same path/method/config).
    all_plugins: dict = {}
    for tmpl in templates.values():
        for name, defn in tmpl["initialize"]["plugins"].items():
            all_plugins.setdefault(name, defn)

    # Use aggregation from the first template (identical across all profiles).
    aggregation = next(iter(templates.values()))["aggregation"]

    manifest: dict = {
        "name": f"job{jobid}",
        "description": f"Carbon estimate for job {jobid}",
        "aggregation": aggregation,
        "initialize": {"plugins": all_plugins},
        "tree": {
            "children": {
                nd.node: _build_node(nd, templates[nd.node], grid_carbon_intensity)
                for nd in node_data_list
            }
        },
    }

    return manifest


def _build_node(
    node_data: NodeData,
    template: dict,
    grid_carbon_intensity: float,
) -> dict:
    observations = synthesize(node_data.node, node_data.metrics)
    return {
        "pipeline": template["pipeline"],
        "defaults": {
            "grid_carbon_intensity": grid_carbon_intensity,
            "job": 1,
            "cpu_total": node_data.cpu_total,
            "mem_total": node_data.mem_total,
            "cpu_allocated": node_data.cpu_allocated,
            "mem_allocated": node_data.mem_allocated,
        },
        "inputs": [dataclasses.asdict(obs) for obs in observations],
    }
