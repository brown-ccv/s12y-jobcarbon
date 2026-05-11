from jobcarbon.alignment import align
from jobcarbon.models import NodeData


def _make_series(timestamps, values=None):
    if values is None:
        values = [1.0] * len(timestamps)
    return {"values": [[ts, v] for ts, v in zip(timestamps, values)]}


def test_alignment_variable_duration():
    node = "nodeA"
    metrics = {
        "cpu_power": [_make_series([0, 60, 120])],
        "dram_power": [_make_series([0, 120])],
    }
    nd = NodeData(
        node=node,
        profile=None,
        metrics=metrics,
        cpu_total=1,
        mem_total=1,
        cpu_allocated=1,
        mem_allocated=1,
    )
    obs = align(nd, step_seconds=60)
    assert len(obs) == 2
    assert obs[0].duration == 120
    assert obs[1].duration == 60


def test_alignment_non_positive_interval_raises():
    metrics_bad = {
        "cpu_power": [_make_series([0, 60, 60])],
        "dram_power": [_make_series([0, 60])],
    }
    nd = NodeData(
        node="n",
        profile=None,
        metrics=metrics_bad,
        cpu_total=1,
        mem_total=1,
        cpu_allocated=1,
        mem_allocated=1,
    )
    try:
        align(nd, step_seconds=60)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "non-positive duration" in str(e)
