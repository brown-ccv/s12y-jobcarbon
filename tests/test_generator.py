from conftest import prom_series
from generator import generate_manifest
from models import NodeData
from registry import NodeProfile


def _make_node_data(node="node1", profile=NodeProfile.HOST_ONLY):
    """Build a minimal NodeData for generator tests with a single host_power timeseries."""
    return NodeData(
        node=node,
        profile=profile,
        metrics={"host_power": [prom_series(f"{node}:9191", [(1000, 200.0)])]},
        cpu_total=32,
        mem_total=131072 * 1024 * 1024,
        cpu_allocated=8,
        mem_allocated=16384,
    )


def test_generate_manifest_one_child_per_node():
    node_data_list = [_make_node_data("node1"), _make_node_data("node2")]
    manifest = generate_manifest("42", node_data_list, grid_carbon_intensity=100.0)
    assert set(manifest["tree"]["children"].keys()) == {"node1", "node2"}


def test_generate_manifest_plugin_union_no_duplicates():
    node_data_list = [
        _make_node_data("node1", NodeProfile.FULL),
        _make_node_data("node2", NodeProfile.HOST_ONLY),
    ]
    manifest = generate_manifest("42", node_data_list, grid_carbon_intensity=100.0)
    plugin_keys = list(manifest["initialize"]["plugins"].keys())
    assert len(plugin_keys) == len(set(plugin_keys))
