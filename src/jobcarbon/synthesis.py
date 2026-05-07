import logging
from dataclasses import dataclass

import pandas as pd

from .models import NodeData, Observation

logger = logging.getLogger(__name__)


@dataclass
class MetricFrame:
    metric_id: str
    frame: pd.DataFrame


def _to_dataframe(metric_id: str, results: list[dict]) -> pd.DataFrame:
    """Unpack a Prometheus result list into a DataFrame"""
    rows = [
        {"timestamp": int(ts), metric_id: float(val)}
        for series in results
        for ts, val in series["values"]
    ]
    if not rows:
        return pd.DataFrame(columns=pd.Index(["timestamp", metric_id]))
    return pd.DataFrame(rows)


def synthesize(node_data: NodeData, step_seconds: int) -> list[Observation]:
    """Combine per-metric Prometheus results into a list of Observations"""
    metric_frames = [
        MetricFrame(metric_id=metric_id, frame=_to_dataframe(metric_id, results))
        for metric_id, results in node_data.metrics.items()
    ]

    combined = metric_frames[0].frame
    for mf in metric_frames[1:]:
        combined = combined.merge(mf.frame, on="timestamp", how="inner")
    combined = combined.sort_values("timestamp")

    return [
        Observation(
            timestamp=row["timestamp"],
            duration=step_seconds,
            cpu_power=row.get("cpu_power"),
            dram_power=row.get("dram_power"),
            host_power=row.get("host_power"),
            gpu_power=row.get("gpu_power"),
        )
        for row in combined.to_dict(orient="records")
    ]
