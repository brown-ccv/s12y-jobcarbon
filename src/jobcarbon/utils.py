import os
from pathlib import Path
from typing import Any


def output_text(content: str, path: Path | None) -> None:
    """Print to file or stdout."""
    if path is not None:
        path.write_text(content)
    else:
        print(content, end="")


def get_config_file() -> Path:
    """Return the config file path, searching standard locations."""
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")

    try:
        spack_prefix = Path(__file__).resolve().parents[4]
        spack_share_cfg = spack_prefix / "share" / "jobcarbon" / "config.toml"
    except IndexError:
        spack_share_cfg = None

    files = [
        os.environ.get("JOBCARBON_CONFIG"),
        Path(xdg_cfg) / "jobcarbon" / "config.toml",
        spack_share_cfg,
        Path("/etc/jobcarbon/config.toml"),
    ]

    for p in files:
        if p and Path(p).is_file():
            return Path(p)

    raise FileNotFoundError("No jobcarbon config file found")


def nearest_neighbor(key: int, lookup: dict[int, Any]) -> Any:
    """Return the value from lookup at the closest matching key."""
    nearest = min(lookup.keys(), key=lambda t: abs(t - key))
    return lookup[nearest]
