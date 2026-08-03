import textwrap
from pathlib import Path

import pytest

from jobcarbon.config import Config, parse_hostlist


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "jobcarbon.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_parse_hostlist():
    assert parse_hostlist("gpu1414") == ["gpu1414"]
    assert parse_hostlist("gpu[2101-2103,2116]") == [
        "gpu2101",
        "gpu2102",
        "gpu2103",
        "gpu2116",
    ]
    # zero-padding preserved
    assert parse_hostlist("node[001-003]") == ["node001", "node002", "node003"]
    # several comma-joined bracket expressions
    assert parse_hostlist("gpu[1-2],cpu[10-11]") == [
        "gpu1",
        "gpu2",
        "cpu10",
        "cpu11",
    ]


def test_config_load_node_map_basic(tmp_path):
    toml = _write_toml(
        tmp_path,
        """
        grid_carbon_intensity = 381
        [[gpus]]
        gpu_model = "NVIDIA A100"
        pcf_carbon_per_gpu = 127600.0
        nodes = "gpu[1-2]"
        [[gpus]]
        gpu_model = "NVIDIA H100"
        pcf_carbon_per_gpu = 164000.0
        nodes = "gpu3"
    """,
    )
    cfg = Config.load(toml)
    assert cfg.gpu_for_node("gpu1")["gpu_model"] == "NVIDIA A100"
    assert cfg.gpu_for_node("gpu2")["gpu_model"] == "NVIDIA A100"
    assert cfg.gpu_for_node("gpu3")["gpu_model"] == "NVIDIA H100"


def test_config_load_node_map_duplicate_raises(tmp_path):
    toml = _write_toml(
        tmp_path,
        """
        grid_carbon_intensity = 381
        [[gpus]]
        gpu_model = "NVIDIA A100"
        pcf_carbon_per_gpu = 127600.0
        nodes = "gpu1"
        [[gpus]]
        gpu_model = "NVIDIA H100"
        pcf_carbon_per_gpu = 164000.0
        nodes = "gpu1"
    """,
    )
    with pytest.raises(ValueError, match="duplicate node hostname 'gpu1'"):
        Config.load(toml)


def test_config_load_node_map_empty(tmp_path):
    toml = _write_toml(tmp_path, "grid_carbon_intensity = 381\n")
    cfg = Config.load(toml)
    assert cfg.gpu_for_node("gpu1") is None
