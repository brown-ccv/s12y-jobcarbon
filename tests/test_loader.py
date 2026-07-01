import pytest
import responses
from unittest.mock import MagicMock

from conftest import prom_instant, prom_series
from jobcarbon.config import (
    Config,
    PROCESS_SCALARS,
    MEM_SCALARS,
    DEFAULT_YIELD_FACTOR,
    DEFAULT_ELECTRICITY_MAPS_ZONE,
    _years_to_seconds,
)
from jobcarbon.loader import _get_nodes, _process_node, process_job
from jobcarbon.models import Window


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
    )
    return Config(**{**defaults, **kwargs})


def _make_process_node_engine(
    cpu=True, dram=False, gpu=False, range_side_effect=None, instant_side_effect=None
):
    """Return a mock engine configured for _process_node tests

    Defaults return timeseries for cpu only (controlled by flags)
    and a scalar 8 for all instant queries. Pass overrides to test specific behaviours
    """
    engine = MagicMock()

    def return_timeseries(metric, window, node="", jobid="", step_seconds=None):
        if metric.id == "cpu_power":
            return [prom_series(f"{node}:9191", [(1000, 1.0)])] if cpu else []
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
    engine.query_lookback.return_value = [
        prom_series("node1:9306", [(1000, 1.0), (1060, 2.0)]),
        prom_series("node2:9306", [(900, 1.0), (1060, 2.0)]),
    ]
    _, window = _get_nodes(engine, "42", 30)
    assert window.start == 900


def test_get_nodes_window_end_is_max_timestamp():
    engine = MagicMock()
    engine.query_lookback.return_value = [
        prom_series("node1:9306", [(1000, 1.0), (1060, 2.0)]),
        prom_series("node2:9306", [(900, 1.0), (1120, 2.0)]),
    ]
    _, window = _get_nodes(engine, "42", 30)
    assert window.end == 1120


def test_get_nodes_raises_when_empty():
    engine = MagicMock()
    engine.query_lookback.return_value = []
    with pytest.raises(ValueError):
        _get_nodes(engine, "42", 30)


@pytest.mark.parametrize(
    "dram,gpu,expected_metrics",
    [
        (False, False, {"cpu_power"}),
        (True, False, {"cpu_power", "dram_power"}),
        (False, True, {"cpu_power", "gpu_power"}),
        (True, True, {"cpu_power", "dram_power", "gpu_power"}),
    ],
)
def test_process_node_metrics(dram, gpu, expected_metrics):
    engine = _make_process_node_engine(dram=dram, gpu=gpu)
    result = _process_node(engine, "node1", "42", Window(start=1000, end=2000))
    assert set(result.metrics.keys()) == expected_metrics


def test_process_node_raises_when_no_cpu_power():
    engine = _make_process_node_engine(cpu=False)
    with pytest.raises(ValueError, match="no cpu_power data"):
        _process_node(engine, "node1", "42", Window(start=1000, end=2000))


def test_process_node_gpu_count_set_when_gpu_present():
    engine = _make_process_node_engine(gpu=True)
    result = _process_node(engine, "node1", "42", Window(start=1000, end=2000))
    assert result.gpu_count == 8


def test_process_node_gpu_count_zero_when_no_gpu():
    engine = _make_process_node_engine(gpu=False)
    result = _process_node(engine, "node1", "42", Window(start=1000, end=2000))
    assert result.gpu_count == 0


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
    engine.query_lookback.return_value = [
        prom_series("node1:9306", [(1000, 1.0)]),
        prom_series("node2:9306", [(1000, 1.0)]),
    ]

    result = process_job(engine, "42", _cfg())
    assert len(result) == 2


@responses.activate
def test_process_job_injects_carbon_intensity():
    responses.add(
        responses.GET,
        "https://api.electricitymap.org/v3/carbon-intensity/past-range",
        json={
            "zone": "US-NE-ISNE",
            "data": [
                {"carbonIntensity": 250, "datetime": "1970-01-01T00:16:40.000Z"},
            ],
        },
    )
    engine = _make_process_node_engine()
    engine.query_lookback.return_value = [
        prom_series("node1:9306", [(1000, 1.0)]),
        prom_series("node2:9306", [(1000, 1.0)]),
    ]

    result = process_job(engine, "42", _cfg(electricity_maps_api_key="test-key"))

    for nd in result:
        assert "grid_carbon_intensity" in nd.metrics
        assert nd.metrics["grid_carbon_intensity"] == [
            {"metric": {}, "values": [(1000, 250.0)]}
        ]


@responses.activate
def test_process_job_skips_intensity_on_fetch_failure():
    responses.add(
        responses.GET,
        "https://api.electricitymap.org/v3/carbon-intensity/past-range",
        status=401,
    )
    engine = _make_process_node_engine()
    engine.query_lookback.return_value = [
        prom_series("node1:9306", [(1000, 1.0)]),
    ]

    result = process_job(engine, "42", _cfg(electricity_maps_api_key="test-key"))

    assert len(result) == 1
    assert "grid_carbon_intensity" not in result[0].metrics
