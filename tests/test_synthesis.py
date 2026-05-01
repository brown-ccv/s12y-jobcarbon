import pandas as pd

from conftest import prom_series
from synthesis import synthesize


def _make_metrics(timestamps=(1000, 1060, 1120), **metric_values):
    """Build a metrics dict with aligned timeseries for each named metric"""
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
