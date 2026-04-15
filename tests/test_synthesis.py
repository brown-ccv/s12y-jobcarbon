import pytest

from conftest import prom_series
from synthesis import synthesize


def _make_metrics(timestamps=(1000, 1060, 1120), **metric_values):
    """Build a metrics dict with aligned timeseries for each named metric."""
    return {
        metric_id: [prom_series("node1:9191", [(ts, val) for ts in timestamps])]
        for metric_id, val in metric_values.items()
    }


def test_synthesize_maps_metric_fields_to_observations():
    metrics = _make_metrics(cpu_power=100.0, dram_power=50.0)
    result = synthesize("node1", metrics)
    assert result[0].cpu_power == 100.0


def test_synthesize_absent_metric_key_becomes_none():
    metrics = _make_metrics(host_power=200.0)
    result = synthesize("node1", metrics)
    assert result[0].gpu_power is None


def test_synthesize_obs_count_matches_timestamps():
    metrics = _make_metrics(timestamps=(1000, 1060, 1120), host_power=200.0)
    result = synthesize("node1", metrics)
    assert len(result) == 3


def test_synthesize_raises_on_misaligned_timestamps():
    metrics = {
        "cpu_power": [
            prom_series("node1:9191", [(1000, 1.0), (1060, 1.0), (1120, 1.0)])
        ],
        "dram_power": [prom_series("node1:9191", [(1000, 1.0), (1060, 1.0)])],
    }
    with pytest.raises(ValueError):
        synthesize("node1", metrics)
