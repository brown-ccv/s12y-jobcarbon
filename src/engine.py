from __future__ import annotations
import os
from dataclasses import dataclass
from math import ceil
from itertools import chain

import requests

from registry import MetricDefinition

PROMETHEUS_URL = os.environ.get("JOBCARBON_PROMETHEUS_URL", "http://localhost:9390")
STEP_SECONDS = int(os.environ.get("JOBCARBON_STEP_SECONDS", 60))
LOOKBACK_DAYS = int(os.environ.get("JOBCARBON_LOOKBACK_DAYS", 30))


@dataclass(frozen=True)
class Window:
    start: int  # unix timestamp in seconds
    end: int  # unix timestamp in seconds

    def chunk(window: Window, step_seconds: int = 60, max_samples: int = 10000) -> list[Window]:
        chunks = []
        cur_start = window.start
        chunk_duration = (max_samples - 1) * step_seconds
        while cur_start <= window.end:
            cur_end = min(cur_start + chunk_duration, window.end)
            chunks.append(Window(cur_start, cur_end))
            cur_start = cur_end + step_seconds
        return chunks


class PrometheusEngine:
    def __init__(
        self, base_url: str = PROMETHEUS_URL, step_seconds: int = STEP_SECONDS
    ):
        self.base_url = base_url.rstrip("/")
        self.step_seconds = step_seconds

    def _parse_response(self, response: requests.Response):
        """Raise on HTTP or Prometheus-level errors and return the result list"""
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise RuntimeError(
                f"Prometheus query failed: {data.get('error', 'unknown error')}"
            )
        return data["data"]["result"]

    def _query_range_standard(
        self, metric: MetricDefinition, window: Window, node: str = "", jobid: str = ""
    ) -> list[dict]:
        """Range query for jobs shorter than the max sample count"""
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
        return self._parse_response(response)

    def _query_range_chunked(
        self,
        metric: MetricDefinition,
        window: Window,
        node: str = "",
        jobid: str = "",
        max_samples: int = 10000,
    ) -> list[dict]:
        """Range query for jobs beyond the max sample count"""
        chunks = Window.chunk(window, self.step_seconds, max_samples)

        return list(
            chain.from_iterable(
                [self._query_range_standard(metric, c, node, jobid) for c in chunks]
            )
        )

    def query_range(
        self,
        metric: MetricDefinition,
        window: Window,
        node: str = "",
        jobid: str = "",
        max_samples: int = 10000,
    ) -> list[dict]:
        """Range query for a specific window of time"""
        duration = window.end - window.start
        needs_chunking = duration / self.step_seconds > max_samples

        if needs_chunking:
            return self._query_range_chunked(metric, window, node, jobid, max_samples)
        else:
            return self._query_range_standard(metric, window, node, jobid)

    def query_instant(
        self, metric: MetricDefinition, time: int, node: str = "", jobid: str = ""
    ) -> list[dict]:
        """Instant query at a specific Unix timestamp"""
        query = metric.query.format(node=node, jobid=jobid)
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query, "time": time},
        )
        return self._parse_response(response)

    def query_lookback(
        self,
        metric: MetricDefinition,
        node: str = "",
        jobid: str = "",
        lookback_days: int = LOOKBACK_DAYS,
    ) -> list[dict]:
        """Lookback query, find metric in the last n days"""
        query = f"{metric.query.format(node=node, jobid=jobid)}[{lookback_days}d]"
        response = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query},
        )
        return self._parse_response(response)
