import logging

import pandas as pd

from .models import NodeData, Observation

logger = logging.getLogger(__name__)


def _to_dataframe(metric_id: str, results: list[dict]) -> pd.DataFrame:
    """Unpack a Prometheus result list into a DataFrame."""
    rows = [
        {"timestamp": int(ts), metric_id: float(val)}
        for series in results
        for ts, val in series["values"]
    ]
    if not rows:
        return pd.DataFrame(columns=pd.Index(["timestamp", metric_id]))
    return pd.DataFrame(rows)


def align(node_data: NodeData, step_seconds: int) -> list[Observation]:
    """Combine per-metric Prometheus results into a list of Observations."""
    metric_frames = [
        _to_dataframe(metric_id, results)
        for metric_id, results in node_data.metrics.items()
    ]

    if not metric_frames:
        raise ValueError("no metric data available to align for node")

    combined, *frames = metric_frames
    for frame in frames:
        combined = combined.merge(frame, on="timestamp", how="inner")
    combined = combined.sort_values("timestamp")

    combined["duration"] = combined["timestamp"].shift(-1) - combined["timestamp"]

    # NOTE(@broarr): Earliest timestamp can't calculate a duration, we have to set it explicitly
    combined.at[combined.index[-1], "duration"] = int(step_seconds)

    if (combined["duration"] <= 0).any():
        raise ValueError("non-positive duration between consecutive timestamps")

    combined["duration"] = combined["duration"].astype(int)

    return [
        Observation(
            timestamp=int(row["timestamp"]),
            duration=int(row["duration"]),
            cpu_power=row.get("cpu_power"),
            dram_power=row.get("dram_power"),
            host_power=row.get("host_power"),
            gpu_power=row.get("gpu_power"),
        )
        for row in combined.to_dict(orient="records")
    ]
