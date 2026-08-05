import logging
from typing import Any, cast

from .config import (
    Config,
    EstimatedGpuSpec,
    is_pcf_spec,
)

logger = logging.getLogger(__name__)

type Inputs = dict[str, Any]


def cpu_embodied_inputs(node: str, socket_count: int, config: Config) -> Inputs:
    """Assemble the CPU/DRAM embodied inputs for a node (die area + constants)."""
    entry = config.cpu_for_node(node)
    if entry is None:
        raise ValueError(f"node '{node}' has no [[cpus]] entry in the config")
    return {
        "die_area_sq_cm": entry["die_area_sq_cm"],
        "socket_count": socket_count,
        "cpu_die_scalar": config.cpu_die_scalar,
        "cpu_base_carbon": config.cpu_base_carbon,
        "dram_die_scalar": config.dram_die_scalar,
        "dram_base_carbon": config.dram_base_carbon,
        "mem_density_gb_per_sq_cm": config.mem_density,
    }


def gpu_embodied_inputs(node: str, gpu_count: int, config: Config) -> Inputs:
    """Assemble the GPU embodied inputs for a node, or {} if it has no GPUs."""
    if not gpu_count:
        return {}
    entry = config.gpu_for_node(node)
    if entry is None:
        raise ValueError(f"node '{node}' has a GPU profile but is not in gpu_config")

    if is_pcf_spec(entry):
        return {
            "gpu_count": gpu_count,
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
        "gpu_count": gpu_count,
        "die_area_sq_cm": estimated["die_area_sq_cm"],
        "vram_gb": estimated["vram_gb"],
        "process_scalar_carbon_per_sq_cm": config.process_scalars[process],
        "mem_scalar_carbon_per_gb": config.mem_scalars[mem_type],
    }


def node_embodied(
    node: str, socket_count: int, mem_total: float, gpu_count: int, config: Config
) -> dict[str, float]:
    """Full node embodied carbon (gCO2eq), unattributed, no time scaling.

    Mirrors the `*-embodied-node` IF plugin arithmetic in Python so a single
    node can be estimated without a job/manifest.
    """
    c = cpu_embodied_inputs(node, socket_count, config)
    cpu = (
        c["die_area_sq_cm"] * c["cpu_die_scalar"] + c["cpu_base_carbon"]
    ) * socket_count
    dram = (mem_total / c["mem_density_gb_per_sq_cm"]) * c["dram_die_scalar"] + c[
        "dram_base_carbon"
    ]

    g = gpu_embodied_inputs(node, gpu_count, config)
    if not g:
        gpu = 0.0
    elif "pcf_carbon_per_gpu" in g:
        gpu = g["pcf_carbon_per_gpu"] * gpu_count
    else:
        per_gpu = (
            g["die_area_sq_cm"] * g["process_scalar_carbon_per_sq_cm"]
            + g["vram_gb"] * g["mem_scalar_carbon_per_gb"]
        )
        gpu = per_gpu * gpu_count

    return {"cpu": cpu, "dram": dram, "gpu": gpu, "total": cpu + dram + gpu}
