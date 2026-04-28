#!/usr/bin/env python3
import logging
import re
import sys
from pathlib import Path
from typing import Annotated

import requests
import typer

from engine import PrometheusEngine
from generator import generate_manifest
from loader import process_job
from yamldump import dump

app = typer.Typer()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Hardware specs keyed by the GPU model string as reported by Slurm GRES.
# die_area_cm2: GPU die area in cm² (sourced from public reverse-engineering analyses)
# vram_gb:      Nominal VRAM capacity in GB
# process_nm:   Lithography node in nm — used to look up process_scalar_kgco2eq_per_cm2
#               via a per-node table in gpu_config.py derived from Boakes et al. IEDM 2023.
#               Yield correction (÷ 0.9) is applied inside the IF pipeline, not here.
# mem_type:     VRAM technology — used to resolve mem_scalar_kgco2eq_per_gb
# pcf_gco2eq:   Manufacturer cradle-to-gate PCF/LCA in gCO2eq (omits regression fields)
#
# Samsung 8N note: GA102/GA107 GPUs use process_nm=8 (Samsung 8N). Samsung 8N is not
#   covered by Boakes et al. (TSMC-specific). gpu_config.py maps process_nm=8 to the
#   TSMC N7 scalar (2.29 kgCO2eq/cm² yield-corrected) as a conservative proxy, with
#   a logged warning.
#
# Sources: TechPowerUp GPU Database, Chips and Cheese die analyses (die areas),
#          Boakes et al. IEDM 2023 (process scalars),
#          Li, Graif, Gupta NeurIPS 2024 workshop (memory scalars)
KNOWN_GPU_SPECS: dict[str, dict] = {
    # --- Turing — TSMC 12N (12nm) ---
    "quadro_rtx_6000": {
        "die_area_cm2": 7.54,   # TU102
        "vram_gb": 24.0,
        "process_nm": 12,
        "mem_type": "gddr6",
    },

    # --- Ampere — Samsung 8N (8nm) ---
    "nvidia_geforce_rtx_3090": {
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 24.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "a5500": {
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 24.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a5000": {
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 24.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "nvidia_a40": {
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 48.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a6000": {
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 48.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "a6000": {                  # same hardware as nvidia_rtx_a6000 on Oscar
        "die_area_cm2": 6.28,   # GA102
        "vram_gb": 48.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },
    "a2": {
        "die_area_cm2": 2.00,   # GA107; 200 mm² per Wikipedia/TechPowerUp
        "vram_gb": 16.0,
        "process_nm": 8,
        "mem_type": "gddr6",
    },

    # --- Ampere — TSMC N7 (7nm) ---
    # PCF from: More than Carbon: Cradle-to-Grave environmental impacts of
    # GenAI training on the Nvidia A100 GPU. Manufacturing (cradle-to-gate)
    # figure; per single GPU.
    "a100": {
        "pcf_gco2eq": 127_600.0,
    },

    # --- Ada Lovelace — TSMC N4 (4nm) ---
    "l40": {
        "die_area_cm2": 6.09,   # AD102; 609 mm² per Wikipedia/TechPowerUp
        "vram_gb": 48.0,
        "process_nm": 4,
        "mem_type": "gddr6",
    },
    "l40s": {
        "die_area_cm2": 6.09,   # AD102; 609 mm² per Wikipedia/TechPowerUp
        "vram_gb": 48.0,
        "process_nm": 4,
        "mem_type": "gddr6",
    },

    # --- Hopper — TSMC N4 (4nm) ---
    # PCF from: NVIDIA HGX H100 product carbon footprint document.
    # System-level cradle-to-gate figure: 1,312 kgCO2eq / 8 GPUs per system
    # = 164 kgCO2eq per GPU. Materials and components = 91% of full lifecycle.
    "h100": {
        "pcf_gco2eq": 164_000.0,
    },
    # No manufacturer PCF available for H100 NVL — uses regression path.
    "nvidia_h100_nvl": {
        "die_area_cm2": 8.14,   # GH100; same die as H100 SXM5
        "vram_gb": 94.0,
        "process_nm": 4,
        "mem_type": "hbm2e",
    },
    "nvidia_gh200_480gb": {
        "die_area_cm2": 8.14,   # GH100 GPU die (excludes Grace CPU die)
        "vram_gb": 480.0,
        "process_nm": 4,
        "mem_type": "hbm3",
    },

    # --- Blackwell — TSMC N4P (4nm) ---
    # PCF from: NVIDIA HGX B200 product carbon footprint document.
    # System-level cradle-to-gate figure: 2,274 kgCO2eq / 8 GPUs per system
    # = 284.25 kgCO2eq per GPU. Materials and components = 94% of full lifecycle.
    "nvidia_b200": {
        "pcf_gco2eq": 284_250.0,
    },
    "nvidia_rtx_pro_6000_blackwell_max-q": {
        "die_area_cm2": 7.50,   # GB202; 750 mm² per Chips and Cheese Blackwell analysis
        "vram_gb": 96.0,
        "process_nm": 4,
        "mem_type": "gddr6",    # GDDR7 — update mem_type when scalar is defined
    },
}


def _run_job(engine: PrometheusEngine, jobid: str, output: Path | None) -> None:
    """Fetch telemetry for jobid and write the manifest to output path, or stdout if None"""
    node_data = process_job(engine, jobid)
    manifest = generate_manifest(jobid, node_data)
    content = dump(manifest)
    if output is None:
        print(content, end="")
        return
    output.write_text(content)


def _parse_hostlist(hostlist: str) -> list[str]:
    """Parses Slurm hostlist strings"""
    # Single host e.g. "gpu1"
    single_host = re.compile(r"^[a-z]+\d+$")
    if single_host.match(hostlist):
        return [hostlist]

    # Prefixed list/range e.g. "gpu[1,3-4]" or "gpu[1-3]"
    match = re.match(r"^([a-z]+)\[(.*)\]$", hostlist)
    if not match:
        raise ValueError(f"Unable to parse hostlist: {hostlist}")

    prefix = match.group(1)
    host_patterns = match.group(2)

    hosts: list[str] = []
    for pattern in host_patterns.split(','):
        if '-' not in pattern:
            # single number
            hosts.append(f"{prefix}{pattern}")
            continue
        start, end = pattern.split('-', 1)
        for i in range(int(start), int(end) + 1):
            hosts.append(f"{prefix}{i}")

    return hosts
        

@app.command()
def init() -> None:
    """Generate config approperiate for cluster, reads sinfo from stdin"""
    if sys.stdin.isatty():
        logger.error("No piped data found. Use:  sinfo -h -o \"%N %G\" | jobcarbon init")
        return

    # NOTE(@broarr): This is hard coded to the GRES column in Oscar's Slurm database
    #   Ideally this would be configurable. Some clusters have VRAM size as a feature,
    #   Oscar does not. We need an explicit map. This will be dependent on cluster config
    gpu_vram_mib = {
        "a2": 15356,
        "quadro_rtx_6000": 24576,
        "geforce3090": 24576,
        "nvidia_geforce_rtx_3090": 24576,
        "nvidia_rtx_a5000": 24564,
        "a5500": 23028,
        "nvidia_a40": 46068,
        "nvidia_rtx_a6000": 49140,
        "a6000": 46068,
        "l40": 46068,
        "l40s": 46068,
        "a100": 81920,
        "nvidia_h100_nvl": 95830,
        "h100": 81559,
        "nvidia_gh200_480gb": 97871,
        "nvidia_b200": 183359
    }

    for line in sys.stdin.readlines():
        pass

    

@app.command()
def batch(
    job_ids: Annotated[list[str], typer.Argument(help="list of Slurm job ids")],
    output_dir: Annotated[
        str | None,
        typer.Option(help="output directory for manifest files (default: ./)"),
    ] = None,
) -> None:
    """Generate multiple manifests in one pass"""
    output_path = Path.cwd()
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

    engine = PrometheusEngine()

    for job_id in job_ids:
        try:
            _run_job(engine, job_id, output_path / f"job{job_id}.yaml")
            logger.info("%s ok", job_id)
        except (ValueError, requests.RequestException) as e:
            logger.error("%s failed: %s", job_id, e)


@app.command()
def main(
    job_id: Annotated[str, typer.Argument(help="Slurm job id")],
    output: Annotated[
        str | None, typer.Option(help="output file for manifest (default: stdout)")
    ] = None,
) -> None:
    """CLI entry point: generate IMP manifests for one or more Slurm jobs"""

    engine = PrometheusEngine()
    output_path = None
    if output:
        output_path = Path.cwd() / output

    _run_job(engine, job_id, output_path)


if __name__ == "__main__":
    app()
