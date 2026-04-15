from dataclasses import dataclass

import pandas as pd

from engine import STEP_SECONDS
from models import Observation


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
        return pd.DataFrame(columns=["timestamp", metric_id])
    return pd.DataFrame(rows)


def _assert_timestamps_aligned(metric_frames: list[MetricFrame]) -> None:
    """Raise if any frame's timestamps diverge from the first"""
    first = metric_frames[0]
    for mf in metric_frames[1:]:
        if not first.frame["timestamp"].equals(mf.frame["timestamp"]):
            raise ValueError(
                f"timestamp mismatch between {first.metric_id} and {mf.metric_id}: "
                f"{first.metric_id} has {first.frame['timestamp'].iloc[0]}..{first.frame['timestamp'].iloc[-1]}, "
                f"{mf.metric_id} has {mf.frame['timestamp'].iloc[0]}..{mf.frame['timestamp'].iloc[-1]}"
            )


def synthesize(node: str, metrics: dict[str, list[dict]]) -> list[Observation]:
    """Combine per-metric Prometheus results into a list of Observations"""
    metric_frames = [
        MetricFrame(metric_id=metric_id, frame=_to_dataframe(metric_id, results))
        for metric_id, results in metrics.items()
    ]

    _assert_timestamps_aligned(metric_frames)

    combined = metric_frames[0].frame
    for mf in metric_frames[1:]:
        combined = combined.merge(mf.frame, on="timestamp", how="inner")

    combined = combined.sort_values("timestamp")

    return [
        Observation(
            timestamp=row["timestamp"],
            duration=STEP_SECONDS,
            node=node,
            cpu_power=row.get("cpu_power"),
            dram_power=row.get("dram_power"),
            host_power=row.get("host_power"),
            gpu_power=row.get("gpu_power"),
        )
        for row in combined.to_dict(orient="records")
    ]
