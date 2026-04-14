import os
from dataclasses import dataclass

import requests

from registry import MetricDefinition

STEP_SECONDS = int(os.environ.get("JOBCARBON_STEP_SECONDS", 60))
LOOKBACK_DAYS = int(os.environ.get("JOBCARBON_LOOKBACK_DAYS", 30))


@dataclass
class Window:
    start: int  # unix timestamp
    end: int    # unix timestamp


class PrometheusEngine:
    def __init__(self, base_url: str, step_seconds: int = STEP_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.step_seconds = step_seconds

    def query_range(self, metric: MetricDefinition, window: Window, node: str = "", jobid: str = "", step_seconds: int | None = None) -> list[dict]:
        query = metric.query.format(node=node, jobid=jobid)
        step = step_seconds if step_seconds is not None else self.step_seconds
        response = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "query": query,
                "start": window.start,
                "end": window.end,
                "step": f"{step}s",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown error')}")
        return data["data"]["result"]

    def query_instant(self, metric: MetricDefinition, time: int, node: str = "", jobid: str = "") -> list[dict]:
        """Instant query at a specific Unix timestamp. Returns a vector — one value per series.

        Each result has 'value: [timestamp, val]' rather than 'values'. Use this for
        scalar metrics (capacity/allocation constants) where a single sample is needed.
        """
        query = metric.query.format(node=node, jobid=jobid)
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query, "time": time},
        )
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown error')}")
        return data["data"]["result"]

    def query(self, metric: MetricDefinition, node: str = "", jobid: str = "", lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
        query = f"{metric.query.format(node=node, jobid=jobid)}[{lookback_days}d]"
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query},
        )
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown error')}")
        return data["data"]["result"]
