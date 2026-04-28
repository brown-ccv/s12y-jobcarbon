import pytest
import responses

from engine import PrometheusEngine, Window
from registry import MetricDefinition

BASE_URL = "http://localhost:9999"
METRIC = MetricDefinition(id="test", query="test_metric{{node='{node}'}}")
PROM_ERROR = {
    "status": "error",
    "errorType": "bad_data",
    "error": "something went wrong",
}


@responses.activate
def test_query_range_raises_on_error_status():
    responses.add(responses.GET, f"{BASE_URL}/api/v1/query_range", json=PROM_ERROR)
    engine = PrometheusEngine(BASE_URL)
    with pytest.raises(RuntimeError):
        engine.query_range(METRIC, window=Window(start=1000, end=2000), node="node1")


@responses.activate
def test_query_instant_raises_on_error_status():
    responses.add(responses.GET, f"{BASE_URL}/api/v1/query", json=PROM_ERROR)
    engine = PrometheusEngine(BASE_URL)
    with pytest.raises(RuntimeError):
        engine.query_instant(METRIC, time=1000, node="node1")

def test_window_chunking():
    window = Window(1, 7)
    chunks = Window.chunk(window, 1, 3)
    assert chunks[0].start == 1 and chunks[0].end == 3
    assert chunks[1].start == 4 and chunks[1].end == 6
    assert chunks[2].start == 7 and chunks[2].end == 7
