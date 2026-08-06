import pytest

from jobcarbon.config import (
    Config,
    CPU_BASE_CARBON,
    CPU_DIE_SCALAR,
    DRAM_BASE_CARBON,
    DRAM_DIE_SCALAR,
    DEFAULT_ELECTRICITY_MAPS_ZONE,
    DEFAULT_MEM_DENSITY,
    PROCESS_SCALARS,
    MEM_SCALARS,
    _years_to_seconds,
)
from jobcarbon.embodied import node_embodied

_CPU_DIE = 6.94
_PCF_ENTRY = {"gpu_model": "Test PCF", "pcf_carbon_per_gpu": 150000.0}
_LCA_ENTRY = {"gpu_model": "Test LCA", "lca_carbon_per_gpu": 127600.0}
_ESTIMATED_ENTRY = {
    "gpu_model": "Test EST",
    "die_area_sq_cm": 8.15,
    "vram_gb": 80.0,
    "process": "tsmc-n7",
    "mem_type": "hbm2",
}


def _cfg(gpu_entry: dict | None = None) -> Config:
    return Config(
        grid_carbon_intensity=381.0,
        cpu_lifespan_seconds=_years_to_seconds(5),
        gpu_lifespan_seconds=_years_to_seconds(5),
        prometheus_url="http://localhost:9390",
        step_seconds=60,
        lookback_days=30,
        max_samples=10000,
        electricity_maps_zone=DEFAULT_ELECTRICITY_MAPS_ZONE,
        electricity_maps_api_key=None,
        process_scalars=PROCESS_SCALARS,
        mem_scalars=MEM_SCALARS,
        mem_density=DEFAULT_MEM_DENSITY,
        node_map={"node1": gpu_entry} if gpu_entry else {},
        cpu_node_map={"node1": {"cpu_model": "Test", "die_area_sq_cm": _CPU_DIE}},
        embodied=True,
    )


def _expected_cpu(sockets: int) -> float:
    return (_CPU_DIE * CPU_DIE_SCALAR + CPU_BASE_CARBON) * sockets


def _expected_dram(mem: float) -> float:
    return (mem / DEFAULT_MEM_DENSITY) * DRAM_DIE_SCALAR + DRAM_BASE_CARBON


def test_server_only_no_gpu():
    r = node_embodied(
        "node1", socket_count=2, mem_total=128, gpu_count=0, config=_cfg()
    )
    assert r["gpu"] == 0.0
    assert r["cpu"] == pytest.approx(_expected_cpu(2))
    assert r["dram"] == pytest.approx(_expected_dram(128))
    assert r["total"] == pytest.approx(r["cpu"] + r["dram"])


def test_pcf_gpu():
    r = node_embodied(
        "node1", socket_count=2, mem_total=512, gpu_count=4, config=_cfg(_PCF_ENTRY)
    )
    assert r["gpu"] == pytest.approx(150000.0 * 4)
    assert r["total"] == pytest.approx(r["cpu"] + r["dram"] + r["gpu"])


def test_lca_gpu():
    r = node_embodied(
        "node1", socket_count=2, mem_total=512, gpu_count=4, config=_cfg(_LCA_ENTRY)
    )
    assert r["gpu"] == pytest.approx(127600.0 * 4)
    assert r["total"] == pytest.approx(r["cpu"] + r["dram"] + r["gpu"])


def test_estimated_gpu():
    r = node_embodied(
        "node1",
        socket_count=2,
        mem_total=512,
        gpu_count=2,
        config=_cfg(_ESTIMATED_ENTRY),
    )
    # (8.15 * 2060 [tsmc-n7] + 80 * 900 [hbm2]) * 2
    assert r["gpu"] == pytest.approx((8.15 * 2060 + 80 * 900) * 2)
    assert r["total"] == pytest.approx(r["cpu"] + r["dram"] + r["gpu"])


def test_unknown_node_raises():
    with pytest.raises(ValueError, match="no \\[\\[cpus\\]\\]"):
        node_embodied("ghost", socket_count=1, mem_total=64, gpu_count=0, config=_cfg())
