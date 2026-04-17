from jobcarbon import _parse_hostlist


def test_parse_hostlist_gpu_bracket_single():
    assert _parse_hostlist("gpu[1]") == ["gpu1"]


def test_parse_hostlist_gpu_no_bracket():
    assert _parse_hostlist("gpu1") == ["gpu1"]


def test_parse_hostlist_gpu_range():
    assert _parse_hostlist("gpu[1-3]") == ["gpu1", "gpu2", "gpu3"]


def test_parse_hostlist_gpu_mixed():
    assert _parse_hostlist("gpu[1,3-4]") == ["gpu1", "gpu3", "gpu4"]
