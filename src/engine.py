from dataclasses import dataclass

import requests

from registry import MetricDefinition

STEP_SECONDS = 60


@dataclass
class Window:
    start: int  # unix timestamp
    end: int    # unix timestamp


class PrometheusEngine:
    def __init__(self, base_url: str, step_seconds: int = STEP_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.step_seconds = step_seconds

    def query_range(self, metric: MetricDefinition, window: Window, node: str = "", jobid: str = "") -> list[dict]:
        query = metric.query.format(node=node, jobid=jobid)
        response = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "query": query,
                "start": window.start,
                "end": window.end,
                "step": f"{self.step_seconds}s",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown error')}")
        return data["data"]["result"]
