import os
from pathlib import Path


def output_text(content: str, path: Path | None) -> None:
    """Print to file or stdout"""
    if path is not None:
        path.write_text(content)
    else:
        print(content, end="")


def get_config_file() -> Path:
    """Return the config file path, searching standard locations."""
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    files = [
        os.environ.get("JOBCARBON_CONFIG"),
        Path(xdg_cfg) / "jobcarbon" / "config.toml",
        Path("/etc/jobcarbon/config.toml"),
    ]
    for p in files:
        if p and Path(p).is_file():
            return Path(p)
    raise FileNotFoundError("No jobcarbon config file found")
