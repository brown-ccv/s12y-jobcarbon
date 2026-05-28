import responses

from jobcarbon.electricity_maps import fetch_carbon_intensity_metric


@responses.activate
def test_fetch_carbon_intensity_metric():
    responses.add(
        responses.GET,
        "https://api.electricitymap.org/v3/carbon-intensity/past-range",
        json={
            "zone": "US-NE-ISNE",
            "data": [
                {
                    "zone": "US-NE-ISNE",
                    "carbonIntensity": 200,
                    "datetime": "2026-05-18T16:00:00.000Z",
                    "isEstimated": False,
                },
                {
                    "zone": "US-NE-ISNE",
                    "carbonIntensity": 204,
                    "datetime": "2026-05-18T17:00:00.000Z",
                    "isEstimated": False,
                },
            ],
        },
    )
    result = fetch_carbon_intensity_metric(
        "US-NE-ISNE", 1779120000, 1779123600, 3600, "test-key"
    )
    assert result == [
        {"metric": {}, "values": [(1779120000, 200.0), (1779123600, 204.0)]}
    ]
