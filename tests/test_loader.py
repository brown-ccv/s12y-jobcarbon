import pytest
from unittest.mock import MagicMock

from conftest import prom_instant, prom_series
from engine import Window
from loader import _get_nodes, _process_node, process_job
from registry import NodeProfile


def _make_process_node_engine(
    dram=False, gpu=False, range_side_effect=None, instant_side_effect=None
):
    """Return a mock engine configured for _process_node tests.

    Defaults return timeseries for all metrics except dram/gpu (controlled by flags)
    and a scalar 8 for all instant queries. Pass overrides to test specific behaviours.
    """
    engine = MagicMock()

    def return_timeseries(metric, window, node="", jobid="", step_seconds=None):
        if metric.id == "dram_power":
            return [prom_series(f"{node}:9191", [(1000, 1.0)])] if dram else []
        if metric.id == "gpu_power":
            return [prom_series(f"{node}:9191", [(1000, 1.0)])] if gpu else []
        return [prom_series(f"{node}:9191", [(1000, 1.0)])]

    def return_instant(metric, time, node="", jobid=""):
        return [prom_instant(f"{node}:9191", 8)]

    engine.query_range.side_effect = range_side_effect or return_timeseries
    engine.query_instant.side_effect = instant_side_effect or return_instant
    return engine


def test_get_nodes_window_start_is_min_timestamp():
    engine = MagicMock()
    engine.query.return_value = [
        prom_series("node1:9306", [(1000, 1.0), (1060, 2.0)]),
        prom_series("node2:9306", [(900, 1.0), (1060, 2.0)]),
    ]
    _, window = _get_nodes(engine, jobid="42", lookback_days=30)
    assert window.start == 900


def test_get_nodes_window_end_is_max_timestamp():
    engine = MagicMock()
    engine.query.return_value = [
        prom_series("node1:9306", [(1000, 1.0), (1060, 2.0)]),
        prom_series("node2:9306", [(900, 1.0), (1120, 2.0)]),
    ]
    _, window = _get_nodes(engine, jobid="42", lookback_days=30)
    assert window.end == 1120


def test_get_nodes_raises_when_empty():
    engine = MagicMock()
    engine.query.return_value = []
    with pytest.raises(ValueError):
        _get_nodes(engine, jobid="42", lookback_days=30)


@pytest.mark.parametrize(
    "dram,gpu,expected_profile",
    [
        (True, False, NodeProfile.FULL),
        (True, True, NodeProfile.FULL_GPU),
        (False, False, NodeProfile.HOST_ONLY),
        (False, True, NodeProfile.HOST_ONLY_GPU),
    ],
)
def test_process_node_profile(dram, gpu, expected_profile):
    engine = _make_process_node_engine(dram=dram, gpu=gpu)
    result = _process_node(engine, "node1", "42", Window(start=1000, end=2000))
    assert result.profile == expected_profile


@pytest.mark.parametrize(
    "empty_metric",
    ["node_cpu_total", "node_mem_total", "cgroup_cpus", "cgroup_mem_total"],
)
def test_process_node_raises_when_capacity_query_empty(empty_metric):
    def return_instant(metric, time, node="", jobid=""):
        if metric.id == empty_metric:
            return []
        return [prom_instant(f"{node}:9191", 8)]

    engine = _make_process_node_engine(instant_side_effect=return_instant)
    with pytest.raises(ValueError):
        _process_node(engine, "node1", "42", Window(start=1000, end=2000))


def test_process_job_returns_one_nodedata_per_node():
    engine = _make_process_node_engine()
    engine.query.return_value = [
        prom_series("node1:9306", [(1000, 1.0)]),
        prom_series("node2:9306", [(1000, 1.0)]),
    ]

    result = process_job(engine, jobid="42")
    assert len(result) == 2
