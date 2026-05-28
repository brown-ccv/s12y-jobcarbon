from jobcarbon.utils import nearest_neighbor


def test_exact_match():
    lookup = {1000: 350.0, 1300: 400.0, 1600: 375.0}
    assert nearest_neighbor(1000, lookup) == 350.0
    assert nearest_neighbor(1300, lookup) == 400.0
    assert nearest_neighbor(1600, lookup) == 375.0


def test_nearest_picks_closer():
    lookup = {1000: 350.0, 1300: 400.0}
    assert nearest_neighbor(1100, lookup) == 350.0
    assert nearest_neighbor(1200, lookup) == 400.0
