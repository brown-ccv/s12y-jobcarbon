from itertools import chain

import requests

from .config import Config
from .models import PromResult, Window
from .registry import MetricDefinition


class PrometheusEngine:
    def __init__(self, config: Config) -> None:
        self.base_url = config.prometheus_url.rstrip("/")
        self.step_seconds = config.step_seconds
        self.max_samples = config.max_samples

    def _parse_response(self, response: requests.Response) -> PromResult:
        """Raise on HTTP or Prometheus-level errors and return the result
        list."""
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(
                f"Prometheus query failed: {data.get('error', 'unknown error')}"
            )
        return data["data"]["result"]

    def _query_range_standard(
        self, metric: MetricDefinition, window: Window, node: str = "", jobid: str = ""
    ) -> PromResult:
        """Range query for jobs shorter than the max sample count."""
        query = metric.query.format(node=node, jobid=jobid, step=self.step_seconds)
        response = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "query": query,
                "start": window.start,
                "end": window.end,
                "step": f"{self.step_seconds}s",
            },
        )
        return self._parse_response(response)

    def _query_range_chunked(
        self,
        metric: MetricDefinition,
        window: Window,
        node: str = "",
        jobid: str = "",
    ) -> PromResult:
        """Range query for jobs beyond the max sample count."""
        chunks = Window.chunk(window, self.step_seconds, self.max_samples)

        return list(
            chain.from_iterable(
                self._query_range_standard(metric, c, node, jobid) for c in chunks
            )
        )

    def query_range(
        self,
        metric: MetricDefinition,
        window: Window,
        node: str = "",
        jobid: str = "",
    ) -> PromResult:
        """Range query for a specific window of time."""
        duration = window.end - window.start
        num_samples = duration // self.step_seconds + 1
        needs_chunking = num_samples > self.max_samples

        if needs_chunking:
            return self._query_range_chunked(metric, window, node, jobid)
        return self._query_range_standard(metric, window, node, jobid)

    def query_instant(
        self, metric: MetricDefinition, time: int, node: str = "", jobid: str = ""
    ) -> PromResult:
        """Instant query at a specific Unix timestamp."""
        query = metric.query.format(node=node, jobid=jobid, step=self.step_seconds)
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query, "time": time},
        )
        return self._parse_response(response)

    def query_lookback(
        self,
        metric: MetricDefinition,
        lookback_days: int,
        node: str = "",
        jobid: str = "",
    ) -> PromResult:
        """Lookback query, find metric in the last n days."""
        query = f"{metric.query.format(node=node, jobid=jobid, step=self.step_seconds)}[{lookback_days}d]"
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query},
        )
        return self._parse_response(response)
