from unittest.mock import patch

import pytest

from jobcarbon.config import (
    Config,
    PROCESS_SCALARS,
    MEM_SCALARS,
    DEFAULT_YIELD_FACTOR,
    DEFAULT_ELECTRICITY_MAPS_ZONE,
    _years_to_seconds,
)
from jobcarbon.models import NodeData, Observation, Window
from jobcarbon.generator import (
    _pipeline_steps,
    _node_defaults,
    _gpu_defaults,
    generate_manifest,
)


def _cfg(**kwargs) -> Config:
    defaults = dict(
        grid_carbon_intensity=381.0,
        cpu_lifespan_seconds=_years_to_seconds(5),
        gpu_lifespan_seconds=_years_to_seconds(5),
        prometheus_url="http://localhost:9390",
        step_seconds=60,
        lookback_days=30,
        max_samples=10000,
        yield_factor=DEFAULT_YIELD_FACTOR,
        electricity_maps_zone=DEFAULT_ELECTRICITY_MAPS_ZONE,
        electricity_maps_api_key=None,
        process_scalars=PROCESS_SCALARS,
        mem_scalars=MEM_SCALARS,
        node_map={},
        embodied=False,
    )
    return Config(**{**defaults, **kwargs})


def _cfg_with_gpu(entry: dict, embodied: bool = False) -> Config:
    return Config(
        grid_carbon_intensity=381.0,
        cpu_lifespan_seconds=_years_to_seconds(5),
        gpu_lifespan_seconds=_years_to_seconds(5),
        prometheus_url="http://localhost:9390",
        step_seconds=60,
        lookback_days=30,
        max_samples=10000,
        yield_factor=DEFAULT_YIELD_FACTOR,
        electricity_maps_zone=DEFAULT_ELECTRICITY_MAPS_ZONE,
        electricity_maps_api_key=None,
        process_scalars=PROCESS_SCALARS,
        mem_scalars=MEM_SCALARS,
        node_map={"node1": entry},
        embodied=embodied,
    )


def _node(metrics: dict | None = None, gpu_count: int = 0) -> NodeData:
    return NodeData(
        node="node1",
        metrics=metrics if metrics is not None else {},
        cpu_total=32,
        mem_total=128,
        cpu_allocated=8,
        mem_allocated=32,
        window=Window(start=1000, end=2000),
        gpu_count=gpu_count,
    )


_PCF_ENTRY = {"gpu_model": "NVIDIA A100", "pcf_carbon_per_gpu": 127600.0}
_ESTIMATED_ENTRY = {
    "gpu_model": "NVIDIA RTX A5000",
    "die_area_sq_cm": 6.28,
    "vram_gb": 24.0,
    "process": "samsung-8n",
    "mem_type": "gddr6",
}
_FAKE_OBS = [Observation(timestamp=1000, duration=60, cpu_power=1.0)]


def test_years_to_seconds():
    assert _years_to_seconds(5) == 5 * 365 * 24 * 3600


# --- operational step selection ---

def test_pipeline_steps_cpu_only():
    steps = _pipeline_steps(_node(), _cfg())
    assert steps == [
        "cpu-share",
        "scale-cpu-power",
        "sum-attributed-power",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ]


def test_pipeline_steps_cpu_dram():
    steps = _pipeline_steps(_node(metrics={"cpu_power": [], "dram_power": []}), _cfg())
    assert steps == [
        "cpu-share",
        "scale-cpu-power",
        "mem-share",
        "scale-dram-power",
        "sum-attributed-power-dram",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ]


def test_pipeline_steps_cpu_gpu():
    steps = _pipeline_steps(_node(gpu_count=2), _cfg())
    assert steps == [
        "cpu-share",
        "scale-cpu-power",
        "sum-attributed-power-gpu",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ]


def test_pipeline_steps_cpu_dram_gpu():
    steps = _pipeline_steps(
        _node(metrics={"cpu_power": [], "dram_power": []}, gpu_count=4), _cfg()
    )
    assert steps == [
        "cpu-share",
        "scale-cpu-power",
        "mem-share",
        "scale-dram-power",
        "sum-attributed-power-dram-gpu",
        "duration-to-hours",
        "calculate-energy",
        "calculate-carbon-operational",
    ]


def test_pipeline_steps_operational_excludes_embodied():
    for node in [
        _node(),
        _node(metrics={"cpu_power": [], "dram_power": []}),
        _node(gpu_count=2),
    ]:
        steps = _pipeline_steps(node, _cfg())
        assert "server-embodied" not in steps
        assert "sum-carbon" not in steps


# --- embodied steps ---

def test_pipeline_steps_embodied_server_only_excludes_gpu():
    steps = _pipeline_steps(_node(), _cfg(embodied=True))
    assert "server-embodied" in steps
    assert "sum-embodied-gpu" not in steps


def test_pipeline_steps_embodied_gpu_pcf_excludes_estimated():
    steps = _pipeline_steps(
        _node(gpu_count=4),
        _cfg_with_gpu(_PCF_ENTRY, embodied=True),
    )
    assert "gpu-embodied-pcf" in steps
    assert "gpu-chip-embodied" not in steps


def test_pipeline_steps_embodied_gpu_estimated_excludes_pcf():
    steps = _pipeline_steps(
        _node(gpu_count=2),
        _cfg_with_gpu(_ESTIMATED_ENTRY, embodied=True),
    )
    assert "gpu-chip-embodied" in steps
    assert "gpu-embodied-pcf" not in steps


def test_pipeline_steps_embodied_gpu_missing_config_raises():
    with pytest.raises(ValueError, match="not in gpu_config"):
        _pipeline_steps(_node(gpu_count=2), _cfg(embodied=True))


# --- defaults ---

def test_node_defaults_always_includes_allocation_fields():
    for node in [
        _node(),
        _node(metrics={"cpu_power": [], "dram_power": []}),
        _node(gpu_count=2),
    ]:
        defaults = _node_defaults(node, _cfg())
        assert {"cpu_total", "cpu_allocated", "mem_total", "mem_allocated"}.issubset(
            defaults.keys()
        )


def test_node_defaults_includes_gci():
    defaults = _node_defaults(_node(), _cfg())
    assert "grid_carbon_intensity" in defaults


def test_node_defaults_omits_gci_when_per_observation_series_present():
    node = _node(metrics={"grid_carbon_intensity": [{"metric": {}, "values": [(1000, 250.0)]}]})
    defaults = _node_defaults(node, _cfg())
    assert "grid_carbon_intensity" not in defaults


def test_node_defaults_embodied_gates_on_flag():
    cfg_off = _cfg(embodied=False)
    cfg_on = _cfg(embodied=True)
    assert "cpu_lifespan_seconds" not in _node_defaults(_node(), cfg_off)
    assert "cpu_lifespan_seconds" in _node_defaults(_node(), cfg_on)


def test_node_defaults_embodied_non_gpu_excludes_gpu_fields():
    defaults = _node_defaults(_node(), _cfg(embodied=True))
    assert "gpu_count" not in defaults
    assert "pcf_carbon_per_gpu" not in defaults


# --- gpu defaults ---

def test_gpu_defaults_pcf_excludes_estimated_fields():
    defaults = _gpu_defaults(
        _node(gpu_count=4), _cfg_with_gpu(_PCF_ENTRY)
    )
    assert "pcf_carbon_per_gpu" in defaults
    assert "die_area_sq_cm" not in defaults


def test_gpu_defaults_estimated_excludes_pcf_fields():
    defaults = _gpu_defaults(
        _node(gpu_count=2), _cfg_with_gpu(_ESTIMATED_ENTRY)
    )
    assert "process_scalar_carbon_per_sq_cm" in defaults
    assert "mem_scalar_carbon_per_gb" in defaults
    assert "pcf_carbon_per_gpu" not in defaults


def test_gpu_defaults_unknown_process_raises():
    entry = {**_ESTIMATED_ENTRY, "process": "intel-4"}
    with pytest.raises(ValueError, match="unknown process"):
        _gpu_defaults(_node(gpu_count=1), _cfg_with_gpu(entry))


def test_gpu_defaults_unknown_mem_type_raises():
    entry = {**_ESTIMATED_ENTRY, "mem_type": "ddr5"}
    with pytest.raises(ValueError, match="unknown mem_type"):
        _gpu_defaults(_node(gpu_count=1), _cfg_with_gpu(entry))


# --- manifest generation ---

def test_generate_manifest_operational_aggregation():
    with patch("jobcarbon.generator.align", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", [_node()], _cfg())
    assert manifest["aggregation"]["metrics"] == [
        "duration",
        "energy",
        "carbon_operational",
    ]


def test_generate_manifest_embodied_aggregation():
    with patch("jobcarbon.generator.align", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", [_node()], _cfg(embodied=True))
    assert "carbon_embodied" in manifest["aggregation"]["metrics"]
    assert "carbon" in manifest["aggregation"]["metrics"]


def test_generate_manifest_plugin_union_across_nodes():
    nodes = [
        _node(metrics={"cpu_power": []}),
        NodeData(
            node="node2",
            metrics={"cpu_power": [], "dram_power": []},
            cpu_total=32,
            mem_total=128,
            cpu_allocated=8,
            mem_allocated=32,
            window=Window(start=1000, end=2000),
        ),
    ]
    with patch("jobcarbon.generator.align", return_value=_FAKE_OBS):
        manifest = generate_manifest("42", nodes, _cfg())
    plugins = manifest["initialize"]["plugins"]
    assert "scale-cpu-power" in plugins
    assert "mem-share" in plugins
    assert "sum-attributed-power-dram" in plugins
