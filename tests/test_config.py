import textwrap
from pathlib import Path

import pytest

from jobcarbon.config import Config, _parse_gres, parse_hostlist, parse_sinfo


def test_parse_hostlist_single():
    assert parse_hostlist("gpu1") == ["gpu1"]


def test_parse_hostlist_bracket_single():
    assert parse_hostlist("gpu[1]") == ["gpu1"]


def test_parse_hostlist_range():
    assert parse_hostlist("gpu[1-3]") == ["gpu1", "gpu2", "gpu3"]


def test_parse_hostlist_mixed():
    assert parse_hostlist("gpu[1,3-4]") == ["gpu1", "gpu3", "gpu4"]


def test_parse_gres_typed():
    assert _parse_gres("gpu:a100:4") == ["a100"]


def test_parse_gres_no_count():
    assert _parse_gres("gpu:h100") == ["h100"]


def test_parse_gres_multi():
    assert _parse_gres("gpu:a100:4,gpu:a100:2") == ["a100", "a100"]


def test_parse_gres_non_gpu_ignored():
    assert _parse_gres("cpu:32,gpu:a100:4") == ["a100"]


def test_parse_gres_bare_gpu_ignored():
    assert _parse_gres("gpu") == []


def test_parse_sinfo_known():
    lines = ["gpu[1-2] gpu:a100:4\n", "gpu3 gpu:a100:8\n"]
    gres_nodes, unknown = parse_sinfo(lines)
    assert gres_nodes["a100"] == {"gpu1", "gpu2", "gpu3"}
    assert unknown == set()


def test_parse_sinfo_unknown():
    lines = ["gpu1 gpu:foobar:2\n"]
    gres_nodes, unknown = parse_sinfo(lines)
    assert gres_nodes["foobar"] == {"gpu1"}
    assert unknown == {"foobar"}


def test_parse_sinfo_malformed_raises():
    with pytest.raises(ValueError, match="malformed sinfo line"):
        parse_sinfo(["justonetoken\n"])


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "jobcarbon.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_config_load_node_map_basic(tmp_path):
    toml = _write_toml(
        tmp_path,
        """
        grid_carbon_intensity = 381
        [[gpus]]
        gpu_model = "NVIDIA A100"
        pcf_carbon_per_gpu = 127600.0
        nodes = ["gpu1", "gpu2"]
        [[gpus]]
        gpu_model = "NVIDIA H100"
        pcf_carbon_per_gpu = 164000.0
        nodes = ["gpu3"]
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
        nodes = ["gpu1"]
        [[gpus]]
        gpu_model = "NVIDIA H100"
        pcf_carbon_per_gpu = 164000.0
        nodes = ["gpu1"]
    """,
    )
    with pytest.raises(ValueError, match="duplicate node hostname 'gpu1'"):
        Config.load(toml)


def test_config_load_node_map_empty(tmp_path):
    toml = _write_toml(tmp_path, "grid_carbon_intensity = 381\n")
    cfg = Config.load(toml)
    assert cfg.gpu_for_node("gpu1") is None
