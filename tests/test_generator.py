from unittest.mock import patch

import pytest

from jobconfig import Config, _years_to_seconds
from models import NodeData, Observation
from registry import NodeProfile
from generator import _pipeline_steps, _node_defaults, _gpu_defaults, generate_manifest


def _cfg(**kwargs) -> Config:
    defaults = dict(
        grid_carbon_intensity=381.0,
        cpu_lifespan_seconds=_years_to_seconds(5),
        gpu_lifespan_seconds=_years_to_seconds(5),
        _node_map={},
        embodied=False,
    )
    return Config(**{**defaults, **kwargs})


def _cfg_with_gpu(entry: dict, embodied: bool = False) -> Config:
    return Config(
        grid_carbon_intensity=381.0,
        cpu_lifespan_seconds=_years_to_seconds(5),
        gpu_lifespan_seconds=_years_to_seconds(5),
        _node_map={"node1": entry},
        embodied=embodied,
    )


def _node(profile: NodeProfile, gpu_count: int = 0) -> NodeData:
    return NodeData(
        node="node1",
        profile=profile,
        metrics={},
        cpu_total=32,
        mem_total=128,
        cpu_allocated=8,
        mem_allocated=32,
        gpu_count=gpu_count,
    )


_PCF_ENTRY = {"gpu_model": "NVIDIA A100", "pcf_gco2eq": 127600.0}
_ESTIMATED_ENTRY = {
    "gpu_model": "NVIDIA RTX A5000",
    "die_area_cm2": 6.28,
    "vram_gb": 24.0,
    "process": "samsung-8n",
    "mem_type": "gddr6",
}
_FAKE_OBS = [Observation(timestamp=1000, duration=60, node="node1", cpu_power=1.0)]


def test_years_to_seconds():
    assert _years_to_seconds(5) == 5 * 365 * 24 * 3600


def test_pipeline_steps_operational_excludes_embodied():
    for profile in NodeProfile:
        steps = _pipeline_steps(_node(profile), _cfg())
        assert "server-embodied" not in steps
        assert "sum-carbon" not in steps


def test_pipeline_steps_host_only_uses_direct_scale():
    steps = _pipeline_steps(_node(NodeProfile.HOST_ONLY), _cfg())
    assert "scale-host-power" in steps
    assert "sum-node-gpu-power" not in steps


def test_pipeline_steps_host_only_gpu_adds_gpu_sum():
    steps = _pipeline_steps(_node(NodeProfile.HOST_ONLY_GPU), _cfg())
    assert "scale-host-power-gpu" in steps
    assert "sum-node-gpu-power" in steps


def test_pipeline_steps_embodied_server_only_excludes_gpu():
    steps = _pipeline_steps(_node(NodeProfile.FULL), _cfg(embodied=True))
    assert "server-embodied" in steps
    assert "sum-embodied-gpu" not in steps


def test_pipeline_steps_embodied_gpu_pcf_excludes_estimated():
    steps = _pipeline_steps(_node(NodeProfile.FULL_GPU, gpu_count=4), _cfg_with_gpu(_PCF_ENTRY, embodied=True))
    assert "gpu-embodied-pcf" in steps
    assert "gpu-chip-embodied" not in steps


def test_pipeline_steps_embodied_gpu_estimated_excludes_pcf():
    steps = _pipeline_steps(_node(NodeProfile.FULL_GPU, gpu_count=2), _cfg_with_gpu(_ESTIMATED_ENTRY, embodied=True))
    assert "gpu-chip-embodied" in steps
    assert "gpu-embodied-pcf" not in steps


def test_pipeline_steps_embodied_gpu_missing_config_raises():
    with pytest.raises(ValueError, match="not in gpu_config"):
        _pipeline_steps(_node(NodeProfile.FULL_GPU, gpu_count=2), _cfg(embodied=True))


def test_node_defaults_operational_only_has_gci():
    defaults = _node_defaults(_node(NodeProfile.FULL), _cfg())
    assert set(defaults.keys()) == {"grid_carbon_intensity"}


def test_node_defaults_embodied_gates_on_flag():
    cfg_off = _cfg(embodied=False)
    cfg_on = _cfg(embodied=True)
    assert "cpu_lifespan_seconds" not in _node_defaults(_node(NodeProfile.FULL), cfg_off)
    assert "cpu_lifespan_seconds" in _node_defaults(_node(NodeProfile.FULL), cfg_on)


def test_node_defaults_embodied_non_gpu_excludes_gpu_fields():
    defaults = _node_defaults(_node(NodeProfile.FULL), _cfg(embodied=True))
    assert "gpu_count" not in defaults
    assert "pcf_carbon_per_gpu" not in defaults


def test_gpu_defaults_pcf_excludes_estimated_fields():
    defaults = _gpu_defaults(_node(NodeProfile.FULL_GPU, gpu_count=4), _cfg_with_gpu(_PCF_ENTRY))
    assert "pcf_carbon_per_gpu" in defaults
    assert "die_area_sq_cm" not in defaults


def test_gpu_defaults_estimated_excludes_pcf_fields():
    defaults = _gpu_defaults(_node(NodeProfile.FULL_GPU, gpu_count=2), _cfg_with_gpu(_ESTIMATED_ENTRY))
    assert "process_scalar_carbon_per_sq_cm" in defaults
    assert "mem_scalar_carbon_per_gb" in defaults
    assert "pcf_carbon_per_gpu" not in defaults


def test_gpu_defaults_unknown_process_raises():
    entry = {**_ESTIMATED_ENTRY, "process": "intel-4"}
    with pytest.raises(ValueError, match="unknown process"):
        _gpu_defaults(_node(NodeProfile.FULL_GPU, gpu_count=1), _cfg_with_gpu(entry))


def test_gpu_defaults_unknown_mem_type_raises():
    entry = {**_ESTIMATED_ENTRY, "mem_type": "ddr5"}
    with pytest.raises(ValueError, match="unknown mem_type"):
        _gpu_defaults(_node(NodeProfile.FULL_GPU, gpu_count=1), _cfg_with_gpu(entry))


def test_generate_manifest_operational_aggregation():
    with patch("generator.synthesize", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", [_node(NodeProfile.FULL)], _cfg())
    assert manifest["aggregation"]["metrics"] == ["duration", "power", "carbon_operational"]


def test_generate_manifest_embodied_aggregation():
    with patch("generator.synthesize", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", [_node(NodeProfile.FULL)], _cfg(embodied=True))
    assert "carbon_embodied" in manifest["aggregation"]["metrics"]
    assert "carbon" in manifest["aggregation"]["metrics"]


def test_generate_manifest_plugin_union_across_profiles():
    nodes = [_node(NodeProfile.FULL), NodeData("node2", NodeProfile.HOST_ONLY, {}, 32, 128, 8, 32)]
    with patch("generator.synthesize", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", nodes, _cfg())
    plugins = manifest["initialize"]["plugins"]
    assert "sum-scaph-power" in plugins
    assert "cpu-share" in plugins
