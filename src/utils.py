import importlib.resources
import os
from pathlib import Path


def output_text(content: str, path: Path | None) -> None:
    """Print to file or stdout"""
    if path is not None:
        path.write_text(content)
    else:
        print(content, end="")


def get_template_dir() -> Path:
    """Return the templates directory, searching standard locations."""
    xdg_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [
        os.environ.get("JOBCARBON_TEMPLATES_DIR"),
        Path(xdg_home) / "jobcarbon" / "templates",
        Path("/usr/local/share/jobcarbon/templates"),
        Path("/usr/share/jobcarbon/templates"),
        Path("/etc/jobcarbon/templates"),
    ]
    for d in dirs:
        if d and Path(d).is_dir() and any(Path(d).iterdir()):
            return Path(d)
    with importlib.resources.as_file(importlib.resources.files("templates")) as p:
        if p.is_dir() and any(p.iterdir()):
            return p
    raise FileNotFoundError("No jobcarbon template directory found")


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
