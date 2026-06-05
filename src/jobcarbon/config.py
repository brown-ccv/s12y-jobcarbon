import logging
import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard, TypedDict, cast

import tomlkit

from .utils import get_config_file

logger = logging.getLogger(__name__)

DEFAULT_GRID_CARBON_INTENSITY = 381  # gCO2eq/kWh, Rhode Island grid average 2023
DEFAULT_YIELD_FACTOR = 0.9
DEFAULT_CPU_LIFESPAN_YEARS = 5
DEFAULT_GPU_LIFESPAN_YEARS = 5
DEFAULT_PROMETHEUS_URL = "http://172.20.11.1:9390"
DEFAULT_STEP_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MAX_SAMPLES = 10000
DEFAULT_ELECTRICITY_MAPS_ZONE = "US-NE-ISNE"

# Raw wafer GWP in gCO2eq/cm2 (pre-yield-correction).
# "samsung-8n" is not in Boakes et al.; TSMC N7 is used as a proxy.
# "tsmc-12n" maps to N14 (architecturally closest).
# "tsmc-n4" / "tsmc-n4p" map to N3 (closest documented in Boakes et al.).
PROCESS_SCALARS: dict[str, float] = {
    "tsmc-n28": 1380,
    "tsmc-n20": 1470,
    "tsmc-n14": 1550,
    "tsmc-12n": 1550,
    "tsmc-n10": 1780,
    "samsung-8n": 2060,
    "tsmc-n7": 2060,
    "tsmc-n5": 2420,
    "tsmc-n4": 2740,
    "tsmc-n4p": 2740,
    "tsmc-n3": 2740,
    "tsmc-n2": 2730,
}

# gCO2eq/GB. Source: Li, Graif, Gupta, HotCarbon 2024
MEM_SCALARS: dict[str, float] = {
    "gddr6": 400,
    "hbm2": 900,
    "hbm2e": 900,
    "hbm3": 900,
}


class PcfGpuSpec(TypedDict):
    gpu_model: str
    pcf_carbon_per_gpu: float


class EstimatedGpuSpec(TypedDict):
    gpu_model: str
    die_area_sq_cm: float
    vram_gb: float
    process: str
    mem_type: str


type GpuSpec = PcfGpuSpec | EstimatedGpuSpec

# Bootstrap seed for jobcarbon-create-config, keyed by Slurm GRES label.
# Used only to populate new TOMLs; not consulted at runtime.
SEED_SPECS: dict[str, GpuSpec] = {
    "quadro_rtx_6000": {
        "gpu_model": "NVIDIA Quadro RTX 6000",
        "die_area_sq_cm": 7.54,
        "vram_gb": 24.0,
        "process": "tsmc-12n",
        "mem_type": "gddr6",
    },
    "nvidia_geforce_rtx_3090": {
        "gpu_model": "NVIDIA GeForce RTX 3090",
        "die_area_sq_cm": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a5500": {
        "gpu_model": "NVIDIA RTX A5500",
        "die_area_sq_cm": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a5000": {
        "gpu_model": "NVIDIA RTX A5000",
        "die_area_sq_cm": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_a40": {
        "gpu_model": "NVIDIA A40",
        "die_area_sq_cm": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a6000": {
        "gpu_model": "NVIDIA RTX A6000",
        "die_area_sq_cm": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a6000": {
        "gpu_model": "NVIDIA RTX A6000",
        "die_area_sq_cm": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a2": {
        "gpu_model": "NVIDIA A2",
        "die_area_sq_cm": 2.00,
        "vram_gb": 16.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a100": {
        "gpu_model": "NVIDIA A100 SXM4 80GB",
        "pcf_carbon_per_gpu": 127_600.0,
    },
    "l40": {
        "gpu_model": "NVIDIA L40",
        "die_area_sq_cm": 6.09,
        "vram_gb": 48.0,
        "process": "tsmc-n4",
        "mem_type": "gddr6",
    },
    "l40s": {
        "gpu_model": "NVIDIA L40S",
        "die_area_sq_cm": 6.09,
        "vram_gb": 48.0,
        "process": "tsmc-n4",
        "mem_type": "gddr6",
    },
    # NOTE(@broarr): Product carbon footprint (pcf) is for the baseboard with
    #   8 GPUs. Divide by 8 to get embodied carbon per GPU
    "h100": {
        "gpu_model": "NVIDIA H100 SXM5 80GB",
        "pcf_carbon_per_gpu": 1_312_000.0 / 8,
    },
    "nvidia_h100_nvl": {
        "gpu_model": "NVIDIA H100 NVL",
        "die_area_sq_cm": 8.14,
        "vram_gb": 94.0,
        "process": "tsmc-n4",
        "mem_type": "hbm2e",
    },
    "nvidia_gh200_480gb": {
        "gpu_model": "NVIDIA GH200 480GB",
        "die_area_sq_cm": 8.14,
        "vram_gb": 480.0,
        "process": "tsmc-n4",
        "mem_type": "hbm3",
    },
    # NOTE(@broarr): Product carbon footprint (pcf) is for the baseboard with
    #   8 GPUs. Divide by 8 to get embodied carbon per GPU
    "nvidia_b200": {
        "gpu_model": "NVIDIA B200",
        "pcf_carbon_per_gpu": 2_274_000.0 / 8,
    },
    "nvidia_rtx_pro_6000_blackwell_max-q": {
        "gpu_model": "NVIDIA RTX Pro 6000 Blackwell Max-Q",
        "die_area_sq_cm": 7.50,
        "vram_gb": 96.0,
        "process": "tsmc-n4p",
        "mem_type": "gddr6",
    },
}


@dataclass(frozen=True)
class Config:
    grid_carbon_intensity: float
    cpu_lifespan_seconds: int
    gpu_lifespan_seconds: int
    prometheus_url: str
    step_seconds: int
    lookback_days: int
    max_samples: int
    yield_factor: float
    electricity_maps_zone: str
    electricity_maps_api_key: str | None
    process_scalars: dict[str, float] = field(repr=False)
    mem_scalars: dict[str, float] = field(repr=False)
    node_map: dict[str, GpuSpec] = field(repr=False)
    embodied: bool = False

    @classmethod
    def load(cls, path: Path | None = None, embodied: bool = False) -> "Config":
        """Load jobcarbon.toml; env vars override file values.

        JOBCARBON_GRID_CARBON_INTENSITY — gCO2eq/kWh
        JOBCARBON_CPU_LIFESPAN_YEARS    — server amortisation period
        JOBCARBON_GPU_LIFESPAN_YEARS    — GPU amortisation period
        JOBCARBON_PROMETHEUS_URL        — Prometheus base URL
        JOBCARBON_STEP_SECONDS          — scrape resolution in seconds
        JOBCARBON_LOOKBACK_DAYS         — range for job/node discovery
        JOBCARBON_MAX_SAMPLES           — max samples per Prometheus query chunk
        JOBCARBON_YIELD_FACTOR          — wafer die yield for chip embodied carbon
        JOBCARBON_ELECTRICITY_MAPS_ZONE — Electricity Maps zone identifier
        JOBCARBON_ELECTRICITY_MAPS_API_KEY — Electricity Maps API key (env only, no file fallback)
        """
        config_path = path if path is not None else get_config_file()
        with config_path.open("rb") as f:
            raw = tomllib.load(f)
        gci = _env_override(
            raw, "grid_carbon_intensity", float, DEFAULT_GRID_CARBON_INTENSITY
        )
        cpu_years = _env_override(
            raw, "cpu_lifespan_years", int, DEFAULT_CPU_LIFESPAN_YEARS
        )
        gpu_years = _env_override(
            raw, "gpu_lifespan_years", int, DEFAULT_GPU_LIFESPAN_YEARS
        )
        prometheus_url = _env_override(
            raw, "prometheus_url", str, DEFAULT_PROMETHEUS_URL
        )
        step_seconds = _env_override(raw, "step_seconds", int, DEFAULT_STEP_SECONDS)
        lookback_days = _env_override(raw, "lookback_days", int, DEFAULT_LOOKBACK_DAYS)
        max_samples = _env_override(raw, "max_samples", int, DEFAULT_MAX_SAMPLES)
        yield_factor = _env_override(raw, "yield_factor", float, DEFAULT_YIELD_FACTOR)
        electricity_maps_zone = _env_override(
            raw, "electricity_maps_zone", str, DEFAULT_ELECTRICITY_MAPS_ZONE
        )
        return cls(
            grid_carbon_intensity=gci,
            cpu_lifespan_seconds=_years_to_seconds(cpu_years),
            gpu_lifespan_seconds=_years_to_seconds(gpu_years),
            prometheus_url=prometheus_url,
            step_seconds=step_seconds,
            lookback_days=lookback_days,
            max_samples=max_samples,
            yield_factor=yield_factor,
            electricity_maps_zone=electricity_maps_zone,
            process_scalars=dict(raw.get("process_scalars", PROCESS_SCALARS)),
            mem_scalars=dict(raw.get("mem_scalars", MEM_SCALARS)),
            node_map=_build_node_map(raw.get("gpus", [])),
            embodied=embodied,
            electricity_maps_api_key=os.environ.get(
                "JOBCARBON_ELECTRICITY_MAPS_API_KEY"
            ),
        )

    def gpu_for_node(self, node: str) -> GpuSpec | None:
        """Return the [[gpus]] entry for a given node hostname, or None."""
        return self.node_map.get(node)

    @classmethod
    def generate(cls, sinfo_lines: list[str]) -> str:
        """Generate a jobcarbon.toml from sinfo -h -o "%N %G" output.

        Logs a warning for each unknown GPU GRES label and returns the
        rendered TOML string.
        """
        gres_nodes, unknown_gres = parse_sinfo(sinfo_lines)

        for gres_name in unknown_gres:
            logger.warning("unknown GPU GRES %r — skipping", gres_name)

        doc = tomlkit.document()
        doc.add(
            tomlkit.comment(
                'Generated by: sinfo -h -o "%N %G" | jobcarbon-create-config'
            )
        )
        doc.add(
            tomlkit.comment("See METHODOLOGY.md for field definitions and sources.")
        )
        doc.add(tomlkit.nl())
        doc.add(
            tomlkit.comment(
                "gCO2eq/kWh — override with JOBCARBON_GRID_CARBON_INTENSITY"
            )
        )
        doc.add("grid_carbon_intensity", tomlkit.item(DEFAULT_GRID_CARBON_INTENSITY))
        doc.add(tomlkit.nl())
        doc.add(
            tomlkit.comment(
                "Prometheus base URL — override with JOBCARBON_PROMETHEUS_URL"
            )
        )
        doc.add("prometheus_url", tomlkit.item(DEFAULT_PROMETHEUS_URL))
        doc.add(
            tomlkit.comment(
                "Scrape resolution in seconds — override with JOBCARBON_STEP_SECONDS"
            )
        )
        doc.add("step_seconds", tomlkit.item(DEFAULT_STEP_SECONDS))
        doc.add(
            tomlkit.comment(
                "Range for job/node discovery — override with JOBCARBON_LOOKBACK_DAYS"
            )
        )
        doc.add("lookback_days", tomlkit.item(DEFAULT_LOOKBACK_DAYS))
        doc.add(
            tomlkit.comment(
                "Max samples per Prometheus query chunk — override with JOBCARBON_MAX_SAMPLES"
            )
        )
        doc.add("max_samples", tomlkit.item(DEFAULT_MAX_SAMPLES))
        doc.add(
            tomlkit.comment(
                "Wafer die yield for chip embodied carbon — override with JOBCARBON_YIELD_FACTOR"
            )
        )
        doc.add("yield_factor", tomlkit.item(DEFAULT_YIELD_FACTOR))
        doc.add(
            tomlkit.comment(
                "Electricity Maps zone identifier — override with JOBCARBON_ELECTRICITY_MAPS_ZONE"
            )
        )
        doc.add("electricity_maps_zone", tomlkit.item(DEFAULT_ELECTRICITY_MAPS_ZONE))
        doc.add(tomlkit.nl())

        process_scalar_table = tomlkit.table()
        process_scalar_table.add(
            tomlkit.comment("Raw wafer GWP in gCO2eq/cm2 (pre-yield-correction).")
        )
        for k, v in PROCESS_SCALARS.items():
            process_scalar_table.add(k, v)
        doc.add("process_scalars", process_scalar_table)
        doc.add(tomlkit.nl())

        memory_scalar_table = tomlkit.table()
        memory_scalar_table.add(tomlkit.comment("gCO2eq/GB."))
        for k, v in MEM_SCALARS.items():
            memory_scalar_table.add(k, v)
        doc.add("mem_scalars", memory_scalar_table)
        doc.add(tomlkit.nl())

        gpus_aot = tomlkit.aot()
        for gres_name, spec in SEED_SPECS.items():
            entry = tomlkit.table()
            for k, v in spec.items():
                entry.add(k, v)
            entry.add("nodes", sorted(gres_nodes.get(gres_name, set())))
            gpus_aot.append(entry)

        doc.add("gpus", gpus_aot)
        return tomlkit.dumps(doc)


def _parse_gres(gres: str) -> list[str]:
    """Parse a Slurm GRES string into GPU type names.

    Handles "gpu:a100:4", "gpu:a100", and bare "gpu". Returns GPU type
    strings (e.g. ["a100"]), or empty list if no GPU entries.
    """
    gpu_types = []
    for part in gres.split(","):
        part = part.strip()
        if not part.startswith("gpu"):
            continue
        fields = part.split(":")
        if len(fields) >= 2 and fields[1]:
            gpu_types.append(fields[1])
    return gpu_types


def _build_node_map(entries: list[dict[str, Any]]) -> dict[str, GpuSpec]:
    """Invert [[gpus]] entries into a node-hostname to entry dict.

    Raises ValueError if the same hostname appears in two entries.
    """
    node_map = {}
    for entry in entries:
        for node in entry.get("nodes", []):
            if node in node_map:
                raise ValueError(
                    f"duplicate node hostname '{node}': appears in entries for "
                    f"'{node_map[node]['gpu_model']}' and '{entry['gpu_model']}'"
                )
            node_map[node] = cast(GpuSpec, entry)
    return node_map


def _years_to_seconds(years: int) -> int:
    """Convert a lifespan in years to whole seconds, using 365 days/year."""
    return years * 365 * 24 * 3600


def _env_override[T](
    raw: dict[str, Any], key: str, cast: Callable[[str], T], default: T
) -> T:
    """Read key from raw (falling back to default), then override with
    JOBCARBON_{KEY} env var if set."""
    value = cast(raw.get(key, default))
    raw_env = os.environ.get("JOBCARBON_" + key.upper())
    return cast(raw_env) if raw_env is not None else value


def is_pcf_spec(entry: GpuSpec) -> TypeGuard[PcfGpuSpec]:
    return "pcf_carbon_per_gpu" in entry


def parse_sinfo(lines: list[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Parse sinfo -h -o "%N %G" output into a gres_name to node-set map.

    Returns (gres_nodes, unknown_gres) where unknown_gres contains GRES
    labels not present in SEED_SPECS. Raises ValueError on malformed
    lines.
    """
    gres_nodes: dict[str, set[str]] = {}
    unknown_gres: set[str] = set()
    for line in lines:
        line = line.strip()
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"malformed sinfo line: {line!r}")
        hostlist, gres_string = parts
        nodes = parse_hostlist(hostlist)
        for gres_name in _parse_gres(gres_string):
            if gres_name not in SEED_SPECS:
                unknown_gres.add(gres_name)
            gres_nodes.setdefault(gres_name, set()).update(nodes)
    return gres_nodes, unknown_gres


def parse_hostlist(hostlist: str) -> list[str]:
    """Parse a Slurm hostlist string into individual hostnames.

    Handles single hosts ("gpu1"), bracket ranges ("gpu[1-3]"), and
    mixed lists ("gpu[1,3-4]").
    """
    if re.match(r"^[a-z]+\d+$", hostlist):
        return [hostlist]

    match = re.match(r"^([a-z]+)\[(.*)\]$", hostlist)
    if not match:
        raise ValueError(f"unable to parse hostlist: {hostlist!r}")

    prefix = match.group(1)
    hosts: list[str] = []
    for pattern in match.group(2).split(","):
        if "-" not in pattern:
            hosts.append(f"{prefix}{pattern}")
        else:
            start, end = pattern.split("-", 1)
            hosts.extend(f"{prefix}{i}" for i in range(int(start), int(end) + 1))
    return hosts
