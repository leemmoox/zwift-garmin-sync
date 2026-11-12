"""End-to-end checks of merged files, for every ride pair in the local FIT cache."""

from functools import lru_cache
from itertools import groupby

import pytest
from fit_tool.fit_file import FitFile

from conftest import ride_params
from fitdata import read_fit
from lib.fit.zwift_track import read_zwift_fit

zwift_ride = lru_cache(maxsize=None)(read_zwift_fit)

# Messages the merge rewrites or may add/remove; everything else must survive unchanged in count
REBUILT = {"record", "lap", "time_in_zone", "unknown_233", "unknown_104"}


@pytest.fixture(params=ride_params())
def ride(request, merged_rides):
    pair = request.param
    watch_path, zwift_path = pair
    return {
        "watch": read_fit(watch_path),
        "zwift": read_fit(zwift_path),
        "zwift_ride": zwift_ride(zwift_path),
        "merged": read_fit(merged_rides(pair)),
    }


def test_device_identity_kept(ride):
    """Garmin must see the merged file as recorded by the watch."""
    assert ride["merged"]["file_id"] == ride["watch"]["file_id"]
    assert ride["merged"]["device_info"] == ride["watch"]["device_info"]


def test_other_messages_kept(ride):
    watch, merged = ride["watch"]["counts"], ride["merged"]["counts"]
    for name in set(watch) - REBUILT:
        assert merged[name] == watch[name], name


def frozen_times(records, min_seconds=30):
    """Timestamps inside runs of one repeated heart rate lasting at least min_seconds."""
    times = set()
    for value, group in groupby(records, key=lambda r: r.get("heart_rate")):
        group = list(group)
        if value and (group[-1]["timestamp"] - group[0]["timestamp"]).total_seconds() + 1 >= min_seconds:
            times.update(r["timestamp"] for r in group)
    return times


def test_watch_sensor_data_kept(ride):
    """Power and cadence are unchanged; heart rate changes only where the watch had none or it was
    frozen, and then to Zwift's value."""
    watch_records = ride["watch"]["record"]
    watch = {r["timestamp"]: r for r in watch_records}
    zwift_hr = {r["timestamp"]: r.get("heart_rate") for r in ride["zwift"]["record"]}
    frozen = frozen_times(watch_records)
    for r in ride["merged"]["record"]:
        original = watch.get(r["timestamp"])
        if not original:
            continue
        for field in ("power", "cadence"):
            assert r.get(field) == original.get(field), (r["timestamp"], field)
        if r.get("heart_rate") != original.get("heart_rate"):
            assert r["heart_rate"] == zwift_hr.get(r["timestamp"]), r["timestamp"]
            assert original.get("heart_rate") is None or r["timestamp"] in frozen, r["timestamp"]


def test_watch_training_data_kept(ride):
    watch, merged = ride["watch"]["session"][0], ride["merged"]["session"][0]
    for field in ("total_training_effect", "total_anaerobic_training_effect", "training_load_peak", "threshold_power"):
        assert merged.get(field) == watch.get(field), field


def test_session_matches_zwift(ride):
    session = ride["merged"]["session"][0]
    assert session["sport"] == "cycling"
    assert session["sub_sport"] == "virtual_activity"
    assert session["total_distance"] == pytest.approx(ride["zwift_ride"].distance, abs=1)


def test_records_cover_zwift_ride_with_gps(ride):
    records = ride["merged"]["record"]
    assert all(r.get("position_lat") is not None and r.get("position_long") is not None for r in records)
    assert all(r.get("distance") is not None for r in records)
    distances = [r["distance"] for r in records]
    assert distances == sorted(distances), "distance must never decrease"


def test_session_bounding_box_contains_route(ride):
    session = ride["merged"]["session"][0]
    for r in ride["merged"]["record"]:
        assert session["swc_lat"] <= r["position_lat"] <= session["nec_lat"]
        assert session["swc_long"] <= r["position_long"] <= session["nec_long"]
    first = ride["merged"]["record"][0]
    assert (session["start_position_lat"], session["start_position_long"]) == (first["position_lat"], first["position_long"])


def test_laps_add_up_to_session(ride):
    session = ride["merged"]["session"][0]
    laps = ride["merged"]["lap"]
    assert session["num_laps"] == len(laps)
    assert [lap["message_index"] for lap in laps] == list(range(len(laps)))
    assert sum(lap["total_elapsed_time"] for lap in laps) == pytest.approx(session["total_elapsed_time"], abs=1)
    assert sum(lap["total_distance"] for lap in laps) == pytest.approx(session["total_distance"], rel=0.005)
    for previous, lap in zip(laps, laps[1:]):
        previous_end = previous["start_time"].timestamp() + previous["total_elapsed_time"]
        assert lap["start_time"].timestamp() == pytest.approx(previous_end, abs=1), "laps must follow each other"
    assert all(lap.get("start_position_lat") is not None for lap in laps)


def test_every_lap_has_time_in_zones(ride):
    laps = ride["merged"]["lap"]
    lap_zones = {z["reference_index"]: z for z in ride["merged"]["time_in_zone"] if z.get("reference_mesg") == "lap"}
    if not any(z.get("reference_mesg") == "lap" for z in ride["watch"]["time_in_zone"]):
        pytest.skip("watch wrote no lap zone data")
    assert sorted(lap_zones) == list(range(len(laps)))
    for lap in laps:
        zones = lap_zones[lap["message_index"]]
        # Power zones cover every second (HR zones skip seconds without an HR reading)
        assert sum(zones["time_in_power_zone"]) == pytest.approx(lap["total_elapsed_time"], abs=2)


def test_zwift_workout_becomes_laps(ride):
    """A Zwift workout (more than one Zwift lap) is reflected one-to-one in the laps."""
    zwift_laps = ride["zwift"]["lap"]
    if len(zwift_laps) < 2:
        pytest.skip("free ride, no Zwift workout laps")
    merged_laps = ride["merged"]["lap"]
    session = ride["merged"]["session"][0]
    session_end = session["start_time"].timestamp() + session["total_elapsed_time"]
    inside = [z for z in zwift_laps if z["start_time"].timestamp() < session_end - 1]
    assert len(merged_laps) == len(inside)
    for merged, zwift in zip(merged_laps[1:], inside[1:]):
        assert merged["start_time"] == zwift["start_time"]


def test_free_ride_auto_laps_use_zwift_distance(ride):
    if len(ride["zwift"]["lap"]) > 1:
        pytest.skip("Zwift workout")
    watch_laps = ride["watch"]["lap"]
    if not all(lap.get("lap_trigger") == "distance" for lap in watch_laps[:-1]) or len(watch_laps) < 2:
        pytest.skip("watch laps are not auto-laps")
    laps = ride["merged"]["lap"]
    assert len(laps) >= 2
    lap_distances = [lap["total_distance"] for lap in laps[:-1]]
    target = sorted(lap_distances)[len(lap_distances) // 2]
    # Every full lap is the auto-lap distance, within one second of riding
    assert all(abs(d - target) <= 20 for d in lap_distances), lap_distances
    assert laps[-1]["total_distance"] <= target + 20


def test_merged_file_reencodes_identically(merged_rides):
    """A merged file loaded and saved again is byte-identical (nothing is left half-written)."""
    pair = next(iter(p.values[0] for p in ride_params() if p.values[0]), None)
    if pair is None:
        pytest.skip("no ride pairs")
    path = merged_rides(pair)
    assert FitFile.from_file(str(path)).to_bytes(preserve=False) == path.read_bytes()
