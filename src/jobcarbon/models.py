from dataclasses import dataclass
from typing import Any

type PromResult = list[dict[str, Any]]


@dataclass(frozen=True)
class Window:
    start: int  # unix timestamp in seconds
    end: int  # unix timestamp in seconds

    @staticmethod
    def chunk(window: "Window", step_seconds: int, max_samples: int) -> list["Window"]:
        chunks = []
        cur_start = window.start
        chunk_duration = (max_samples - 1) * step_seconds
        while cur_start <= window.end:
            cur_end = min(cur_start + chunk_duration, window.end)
            chunks.append(Window(cur_start, cur_end))
            cur_start = cur_end + step_seconds
        return chunks


@dataclass
class NodeData:
    node: str
    window: Window
    metrics: dict[str, PromResult]
    cpu_total: int
    mem_total: int
    cpu_allocated: int
    mem_allocated: int
    socket_count: int = 1
    gpu_count: int = 0


@dataclass
class Observation:
    timestamp: int
    duration: int
    cpu_power: float | None = None
    dram_power: float | None = None
    host_power: float | None = None
    gpu_power: float | None = None
    grid_carbon_intensity: float | None = None
