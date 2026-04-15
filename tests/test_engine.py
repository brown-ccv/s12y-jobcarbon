import pytest
import responses

from engine import PrometheusEngine, Window
from registry import MetricDefinition

BASE_URL = "http://localhost:9999"

METRIC = MetricDefinition(id="test", query="test_metric{{node='{node}'}}", unit="watts")

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
