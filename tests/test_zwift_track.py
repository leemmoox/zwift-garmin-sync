"""ZwiftTrack lookups on synthetic data (no ride files needed)."""

import pytest

from lib.fit.zwift_track import ZwiftTrack


def sample(t, distance, altitude=0.0, speed=5.0):
    return {"t": t, "distance": distance, "speed": speed, "altitude": altitude,
            "lat": None, "long": None, "power": None, "heart_rate": None, "cadence": None}


@pytest.fixture
def track():
    # 10 s ride: 5 m/s, climbs 1 m/s for 5 s, then descends 2 m/s
    altitudes = [0, 1, 2, 3, 4, 5, 3, 1, 0, 0, 0]
    return ZwiftTrack([sample(100 + i, 5.0 * i, a, speed=5.0 + i) for i, a in enumerate(altitudes)])


def test_at_clamps_to_ride(track):
    assert track.at(50)["t"] == 100
    assert track.at(105)["t"] == 105
    assert track.at(500)["t"] == 110


def test_distance_between_starts_from_zero_at_ride_start(track):
    assert track.distance_between(100, 104) == 20.0
    assert track.distance_between(104, 110) == 30.0
    assert track.distance_between(90, 110) == 50.0


def test_climb_between(track):
    assert track.climb_between(100, 110) == (5.0, 5.0)
    assert track.climb_between(100, 105) == (5.0, 0.0)


def test_max_speed_between(track):
    assert track.max_speed_between(100, 103) == 8.0


def test_empty_track_rejected():
    with pytest.raises(ValueError):
        ZwiftTrack([])
