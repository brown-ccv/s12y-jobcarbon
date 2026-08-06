import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict, cast

from .utils import get_config_file

DEFAULT_GRID_CARBON_INTENSITY = 381  # gCO2eq/kWh, Rhode Island grid average 2023
DEFAULT_CPU_LIFESPAN_YEARS = 5
DEFAULT_GPU_LIFESPAN_YEARS = 5
DEFAULT_PROMETHEUS_URL = "http://localhost:9390"
DEFAULT_STEP_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MAX_SAMPLES = 10000
DEFAULT_ELECTRICITY_MAPS_ZONE = "US-NE-ISNE"

# Functional-area GWP in gCO2eq/cm2 (Boakes et al. Fig. 5/7)
# "samsung-8n" is not in Boakes et al.; TSMC N7 is used as a proxy
# "tsmc-12n" maps to N14 (architecturally closest)
# "tsmc-n4" / "tsmc-n4p" map to N3 (closest documented in Boakes et al.)
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

# BoaviztAPI bottom-up embodied carbon constants (gCO2eq, cm2)
# Source: de Rancourt et al., "BoaviztAPI: A Bottom-Up Model to Assess the
# Environmental Impacts of Cloud Services."
CPU_DIE_SCALAR = 1970  # gCO2eq/cm2  (1.97 kgCO2eq/cm2)
CPU_BASE_CARBON = 9140  # gCO2eq per CPU  (9.14 kgCO2eq)
DRAM_DIE_SCALAR = 2200  # gCO2eq/cm2  (2.20 kgCO2eq/cm2)
DRAM_BASE_CARBON = 5220  # gCO2eq per module  (5.22 kgCO2eq)
DEFAULT_MEM_DENSITY = 1.79  # GB/cm2  (RAM die density)


class PcfGpuSpec(TypedDict):
    gpu_model: str
    pcf_carbon_per_gpu: float


class LcaGpuSpec(TypedDict):
    gpu_model: str
    lca_carbon_per_gpu: float


class EstimatedGpuSpec(TypedDict):
    gpu_model: str
    die_area_sq_cm: float
    vram_gb: float
    process: str
    mem_type: str


type GpuSpec = PcfGpuSpec | LcaGpuSpec | EstimatedGpuSpec


class CpuSpec(TypedDict):
    cpu_model: str
    die_area_sq_cm: float


@dataclass(frozen=True)
class Config:
    grid_carbon_intensity: float
    cpu_lifespan_seconds: int
    gpu_lifespan_seconds: int
    prometheus_url: str
    step_seconds: int
    lookback_days: int
    max_samples: int
    electricity_maps_zone: str
    electricity_maps_api_key: str | None
    process_scalars: dict[str, float] = field(repr=False)
    mem_scalars: dict[str, float] = field(repr=False)
    mem_density: float = field(repr=False)
    node_map: dict[str, GpuSpec] = field(repr=False)
    cpu_node_map: dict[str, CpuSpec] = field(repr=False)
    embodied: bool = False
    cpu_die_scalar: float = field(default=CPU_DIE_SCALAR, repr=False)
    cpu_base_carbon: float = field(default=CPU_BASE_CARBON, repr=False)
    dram_die_scalar: float = field(default=DRAM_DIE_SCALAR, repr=False)
    dram_base_carbon: float = field(default=DRAM_BASE_CARBON, repr=False)

    @classmethod
    def load(cls, path: Path | None = None, embodied: bool = False) -> "Config":
        """Load jobcarbon.toml; env vars override file values.

        JOBCARBON_GRID_CARBON_INTENSITY - gCO2eq/kWh
        JOBCARBON_CPU_LIFESPAN_YEARS    - server amortisation period
        JOBCARBON_GPU_LIFESPAN_YEARS    - GPU amortisation period
        JOBCARBON_PROMETHEUS_URL        - Prometheus base URL
        JOBCARBON_STEP_SECONDS          - scrape resolution in seconds
        JOBCARBON_LOOKBACK_DAYS         - range for job/node discovery
        JOBCARBON_MAX_SAMPLES           - max samples per Prometheus query chunk
        JOBCARBON_ELECTRICITY_MAPS_ZONE - Electricity Maps zone identifier
        JOBCARBON_ELECTRICITY_MAPS_API_KEY - Electricity Maps API key (env only, no file fallback)
        JOBCARBON_MEM_DENSITY_GB_PER_SQ_CM - DRAM die density (GB/cm2)
        JOBCARBON_CPU_DIE_SCALAR        - CPU embodied die scalar (gCO2eq/cm2)
        JOBCARBON_CPU_BASE_CARBON       - CPU embodied base carbon (gCO2eq/CPU)
        JOBCARBON_DRAM_DIE_SCALAR       - DRAM embodied die scalar (gCO2eq/cm2)
        JOBCARBON_DRAM_BASE_CARBON      - DRAM embodied base carbon (gCO2eq/module)
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
            electricity_maps_zone=electricity_maps_zone,
            process_scalars=dict(raw.get("process_scalars", PROCESS_SCALARS)),
            mem_scalars=dict(raw.get("mem_scalars", MEM_SCALARS)),
            mem_density=_env_override(
                raw, "mem_density_gb_per_sq_cm", float, DEFAULT_MEM_DENSITY
            ),
            cpu_die_scalar=_env_override(raw, "cpu_die_scalar", float, CPU_DIE_SCALAR),
            cpu_base_carbon=_env_override(
                raw, "cpu_base_carbon", float, CPU_BASE_CARBON
            ),
            dram_die_scalar=_env_override(
                raw, "dram_die_scalar", float, DRAM_DIE_SCALAR
            ),
            dram_base_carbon=_env_override(
                raw, "dram_base_carbon", float, DRAM_BASE_CARBON
            ),
            node_map=_build_node_map(raw.get("gpus", [])),
            cpu_node_map=_build_node_map(raw.get("cpus", []), "cpu_model"),
            embodied=embodied,
            electricity_maps_api_key=os.environ.get(
                "JOBCARBON_ELECTRICITY_MAPS_API_KEY"
            ),
        )

    def gpu_for_node(self, node: str) -> GpuSpec | None:
        """Return the [[gpus]] entry for a given node hostname, or None."""
        return self.node_map.get(node)

    def cpu_for_node(self, node: str) -> CpuSpec | None:
        """Return the [[cpus]] entry for a given node hostname, or None."""
        return self.cpu_node_map.get(node)


def _build_node_map(
    entries: list[dict[str, Any]], model_key: str = "gpu_model"
) -> dict[str, Any]:
    """Invert [[gpus]] or [[cpus]] entries into a node-hostname to entry dict.

    Raises ValueError if the same hostname appears in two entries.
    """
    node_map: dict[str, Any] = {}
    for entry in entries:
        for node in parse_hostlist(entry.get("nodes", "")):
            if node in node_map:
                raise ValueError(
                    f"duplicate node hostname '{node}': appears in entries for "
                    f"'{node_map[node][model_key]}' and '{entry[model_key]}'"
                )
            node_map[node] = entry
    return node_map


def parse_hostlist(hostlist: str) -> list[str]:
    """Parse a Slurm hostlist string into individual hostnames.

    Handles single hosts ("gpu1"), bracket ranges ("gpu[1-3]"), mixed lists
    ("gpu[1,3-4]"), several comma-joined expressions ("gpu[1-4],cpu[10-12]"),
    and zero-padded ranges ("node[001-004]"). Padding is preserved.
    """
    hosts: list[str] = []
    for token in _split_top_level(hostlist):
        prefix, sep, rest = token.partition("[")
        if not sep:
            hosts.append(token)
            continue
        body, _, suffix = rest.partition("]")
        for part in body.split(","):
            part = part.strip()
            if "-" not in part:
                hosts.append(f"{prefix}{part}{suffix}")
                continue
            start, end = part.split("-", 1)
            width = max(len(start), len(end))
            hosts.extend(
                f"{prefix}{i:0{width}d}{suffix}"
                for i in range(int(start), int(end) + 1)
            )
    return hosts


def _split_top_level(spec: str) -> list[str]:
    """Split on commas that fall outside square brackets."""
    tokens: list[str] = []
    depth = start = 0
    for i, ch in enumerate(spec):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            tokens.append(spec[start:i])
            start = i + 1
    tokens.append(spec[start:])
    return [t.strip() for t in tokens if t.strip()]


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


def gpu_direct_carbon(entry: GpuSpec) -> float | None:
    """Per-GPU carbon from a PCF or LCA figure, else None to estimate it."""
    if "pcf_carbon_per_gpu" in entry:
        return cast(PcfGpuSpec, entry)["pcf_carbon_per_gpu"]
    if "lca_carbon_per_gpu" in entry:
        return cast(LcaGpuSpec, entry)["lca_carbon_per_gpu"]
    return None
