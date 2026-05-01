import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "jobcarbon.toml"
_DEFAULT_GRID_CARBON_INTENSITY = 381  # gCO2eq/kWh, Rhode Island grid average 2023

# Yield-corrected gCO2eq/cm2 per TSMC process node
# Source: Boakes et al. IEEE IEDM 2023
# "samsung-8n" is not in Boakes; TSMC N7 is used as a proxy (logged as warning).
# "tsmc-12n" maps to N14 (architecturally closest).
# "tsmc-n4" / "tsmc-n4p" map to N3 (closest documented in Boakes).
PROCESS_SCALARS: dict[str, float] = {
    "tsmc-n28": 1300,
    "tsmc-n20": 1470,
    "tsmc-n14": 1550,
    "tsmc-12n": 1550,
    "tsmc-n10": 1780,
    "samsung-8n": 2220,
    "tsmc-n7": 2220,
    "tsmc-n5": 2420,
    "tsmc-n4": 2740,
    "tsmc-n4p": 2740,
    "tsmc-n3": 2740,
    "tsmc-n2": 2850,
}

# gCO2eq/GB. Source: Li, Graif, Gupta, HotCarbon 2024
MEM_SCALARS: dict[str, float] = {
    "gddr6": 400,
    "hbm2": 900,
    "hbm2e": 900,
    "hbm3": 900,
}

# Bootstrap seed for create-config, keyed by Slurm GRES label.
# Used only to populate new TOMLs; not consulted at runtime.
SEED_SPECS: dict[str, dict] = {
    "quadro_rtx_6000": {
        "gpu_model": "NVIDIA Quadro RTX 6000",
        "die_area_cm2": 7.54,
        "vram_gb": 24.0,
        "process": "tsmc-12n",
        "mem_type": "gddr6",
    },
    "nvidia_geforce_rtx_3090": {
        "gpu_model": "NVIDIA GeForce RTX 3090",
        "die_area_cm2": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a5500": {
        "gpu_model": "NVIDIA RTX A5500",
        "die_area_cm2": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a5000": {
        "gpu_model": "NVIDIA RTX A5000",
        "die_area_cm2": 6.28,
        "vram_gb": 24.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_a40": {
        "gpu_model": "NVIDIA A40",
        "die_area_cm2": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "nvidia_rtx_a6000": {
        "gpu_model": "NVIDIA RTX A6000",
        "die_area_cm2": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a6000": {
        "gpu_model": "NVIDIA RTX A6000",
        "die_area_cm2": 6.28,
        "vram_gb": 48.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a2": {
        "gpu_model": "NVIDIA A2",
        "die_area_cm2": 2.00,
        "vram_gb": 16.0,
        "process": "samsung-8n",
        "mem_type": "gddr6",
    },
    "a100": {
        "gpu_model": "NVIDIA A100 SXM4 80GB",
        "pcf_gco2eq_per_gpu": 127_600.0,
    },
    "l40": {
        "gpu_model": "NVIDIA L40",
        "die_area_cm2": 6.09,
        "vram_gb": 48.0,
        "process": "tsmc-n4",
        "mem_type": "gddr6",
    },
    "l40s": {
        "gpu_model": "NVIDIA L40S",
        "die_area_cm2": 6.09,
        "vram_gb": 48.0,
        "process": "tsmc-n4",
        "mem_type": "gddr6",
    },
    # NOTE(@broarr): Product carbon footprint (pcf) is for the baseboard with
    #   8 GPUs. Divide by 8 to get embodied carbon per GPU
    "h100": {
        "gpu_model": "NVIDIA H100 SXM5 80GB",
        "pcf_gco2eq_per_gpu": 1_312_000.0 / 8,
    },
    "nvidia_h100_nvl": {
        "gpu_model": "NVIDIA H100 NVL",
        "die_area_cm2": 8.14,
        "vram_gb": 94.0,
        "process": "tsmc-n4",
        "mem_type": "hbm2e",
    },
    "nvidia_gh200_480gb": {
        "gpu_model": "NVIDIA GH200 480GB",
        "die_area_cm2": 8.14,
        "vram_gb": 480.0,
        "process": "tsmc-n4",
        "mem_type": "hbm3",
    },
    # NOTE(@broarr): Product carbon footprint (pcf) is for the baseboard with
    #   8 GPUs. Divide by 8 to get embodied carbon per GPU
    "nvidia_b200": {
        "gpu_model": "NVIDIA B200",
        "pcf_gco2eq_per_gpu": 2_274_000.0 / 8,
    },
    "nvidia_rtx_pro_6000_blackwell_max-q": {
        "gpu_model": "NVIDIA RTX Pro 6000 Blackwell Max-Q",
        "die_area_cm2": 7.50,
        "vram_gb": 96.0,
        "process": "tsmc-n4p",
        "mem_type": "gddr6",
    },
}


def _parse_gres(gres: str) -> list[str]:
    """Parse a Slurm GRES string into GPU type names.

    Handles "gpu:a100:4", "gpu:a100", and bare "gpu".
    Returns GPU type strings (e.g. ["a100"]), or empty list if no GPU entries."""
    gpu_types = []
    for part in gres.split(","):
        part = part.strip()
        if not part.startswith("gpu"):
            continue
        fields = part.split(":")
        if len(fields) >= 2 and fields[1]:
            gpu_types.append(fields[1])
    return gpu_types


def parse_sinfo(lines: list[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Parse sinfo -h -o "%N %G" output into a gres_name to node-set map.

    Returns (gres_nodes, unknown_gres) where unknown_gres contains GRES labels
    not present in SEED_SPECS. Raises ValueError on malformed lines.
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

    Handles single hosts ("gpu1"), bracket ranges ("gpu[1-3]"),
    and mixed lists ("gpu[1,3-4]").
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


def _build_node_map(entries: list[dict]) -> dict[str, dict]:
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
            node_map[node] = entry
    return node_map


@dataclass(frozen=True)
class Config:
    grid_carbon_intensity: float
    _node_map: dict[str, dict] = field(repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load jobcarbon.toml; JOBCARBON_GRID_CARBON_INTENSITY overrides the file value."""
        config_path = path or Path(
            os.environ.get("JOBCARBON_CONFIG", _DEFAULT_CONFIG_PATH)
        )
        with config_path.open("rb") as f:
            raw = tomllib.load(f)
        gci = float(raw.get("grid_carbon_intensity", _DEFAULT_GRID_CARBON_INTENSITY))
        env_gci = os.environ.get("JOBCARBON_GRID_CARBON_INTENSITY")
        if env_gci is not None:
            gci = float(env_gci)
        return cls(
            grid_carbon_intensity=gci, _node_map=_build_node_map(raw.get("gpus", []))
        )

    def gpu_for_node(self, node: str) -> dict | None:
        """Return the [[gpus]] entry for a given node hostname, or None."""
        return self._node_map.get(node)

    @classmethod
    def generate(cls, sinfo_lines: list[str]) -> str:
        """Generate a jobcarbon.toml from sinfo -h -o "%N %G" output.

        Logs a warning for each unknown GPU GRES label and returns the rendered
        TOML string.
        """
        gres_nodes, unknown_gres = parse_sinfo(sinfo_lines)

        for gres_name in unknown_gres:
            logger.warning("unknown GPU GRES %r — skipping", gres_name)

        doc = tomlkit.document()
        doc.add(
            tomlkit.comment(
                'Generated by: sinfo -h -o "%N %G" | jobcarbon create-config'
            )
        )
        doc.add(
            tomlkit.comment("See METHODOLOGY.md for field definitions and sources.")
        )
        doc.add(tomlkit.nl())
        doc.add("grid_carbon_intensity", tomlkit.item(_DEFAULT_GRID_CARBON_INTENSITY))
        doc.add(
            tomlkit.comment(
                "gCO2eq/kWh — override with JOBCARBON_GRID_CARBON_INTENSITY"
            )
        )
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
