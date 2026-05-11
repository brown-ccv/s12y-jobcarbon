from dataclasses import dataclass

from .registry import NodeProfile


@dataclass
class NodeData:
    node: str
    profile: NodeProfile | None
    metrics: dict[str, list[dict[str, str]]]
    cpu_total: int
    mem_total: int
    cpu_allocated: int
    mem_allocated: int
    gpu_count: int = 0


@dataclass
class Observation:
    timestamp: int
    duration: int
    cpu_power: float | None = None
    dram_power: float | None = None
    host_power: float | None = None
    gpu_power: float | None = None
