def prom_series(instance, values):
    """One Prometheus range result dict: metric label + list of [timestamp, value] pairs."""
    return {
        "metric": {"instance": instance},
        "values": [[ts, str(val)] for ts, val in values],
    }


def prom_instant(instance, value):
    """One Prometheus instant result dict: metric label + single [timestamp, value] pair."""
    return {
        "metric": {"instance": instance},
        "value": [1000, str(value)],
    }
