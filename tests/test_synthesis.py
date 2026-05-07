from conftest import prom_series
from jobcarbon.models import NodeData
from jobcarbon.registry import NodeProfile
from jobcarbon.synthesis import synthesize


def _make_node_data(timestamps=(1000, 1060, 1120), **metric_values) -> NodeData:
    """Build a NodeData with aligned timeseries for each named metric"""
    metrics = {
        metric_id: [prom_series("node1:9191", [(ts, val) for ts in timestamps])]
        for metric_id, val in metric_values.items()
    }
    return NodeData(
        node="node1",
        profile=NodeProfile.FULL,
        metrics=metrics,
        cpu_total=32,
        mem_total=128,
        cpu_allocated=8,
        mem_allocated=32,
    )


def test_synthesize_maps_metric_fields_to_observations():
    node_data = _make_node_data(cpu_power=100.0, dram_power=50.0)
    result = synthesize(node_data, 60)
    assert result[0].cpu_power == 100.0


def test_synthesize_absent_metric_key_becomes_none():
    node_data = _make_node_data(host_power=200.0)
    result = synthesize(node_data, 60)
    assert result[0].gpu_power is None
