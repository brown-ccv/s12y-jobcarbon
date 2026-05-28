from datetime import datetime, timezone
from enum import Enum

import requests

from .models import PromResult
from .utils import nearest_neighbor

BASE_URL = "https://api.electricitymap.org"
SECONDS_PER_DAY = 86400


class Granularity(str, Enum):
    FIVE_MINUTES = "5_minutes"
    FIFTEEN_MINUTES = "15_minutes"
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


MAX_DAYS_PER_GRANULARITY: dict[Granularity, int] = {
    Granularity.FIVE_MINUTES: 1,
    Granularity.FIFTEEN_MINUTES: 3,
    Granularity.HOURLY: 10,
    Granularity.DAILY: 365,
    Granularity.MONTHLY: 365,
    Granularity.YEARLY: 3650,
}


def _fetch_raw_intensity(
    zone: str, start: int, end: int, api_key: str, granularity: Granularity
) -> dict[int, float]:
    """Fetch raw carbon intensity points from Electricity Maps for [start, end]."""
    start_iso = datetime.fromtimestamp(start, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end, tz=timezone.utc).isoformat()

    params: dict[str, str] = {
        "zone": zone,
        "start": start_iso,
        "end": end_iso,
        "temporalGranularity": granularity,
    }

    response = requests.get(
        f"{BASE_URL}/v3/carbon-intensity/past-range",
        params=params,
        headers={"auth-token": api_key},
        timeout=30,
    )

    response.raise_for_status()
    return {
        int(datetime.fromisoformat(e["datetime"]).timestamp()): float(
            e["carbonIntensity"]
        )
        for e in response.json().get("data", [])
    }


def fetch_carbon_intensity_metric(
    zone: str,
    start: int,
    end: int,
    step_seconds: int,
    api_key: str,
    granularity: Granularity = Granularity.FIVE_MINUTES,
) -> PromResult:
    """Fetch carbon intensity for zone, expanded to step_seconds resolution."""
    # API limits are documented in days; convert to seconds to chunk by unix timestamp
    max_window = MAX_DAYS_PER_GRANULARITY[granularity] * SECONDS_PER_DAY
    raw: dict[int, float] = {}
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + max_window, end)
        raw.update(
            _fetch_raw_intensity(zone, chunk_start, chunk_end, api_key, granularity)
        )
        chunk_start = chunk_end + 1

    if not raw:
        raise ValueError(
            f"Electricity Maps returned no carbon intensity data for zone {zone!r} "
            f"between {start} and {end}"
        )

    values = [
        (ts, nearest_neighbor(ts, raw)) for ts in range(start, end + 1, step_seconds)
    ]
    return [{"metric": {}, "values": values}]
