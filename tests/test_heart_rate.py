"""Frozen or missing watch heart rate is repaired from Zwift's."""

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage

from conftest import RIDE_PAIRS, merge_quietly
from fitdata import read_fit
from lib.fit.heart_rate import HeartRateRepair
from lib.fit.utils import set_field
from lib.fit.zwift_track import read_zwift_fit


def ride_with_good_heart_rate():
    """A ride where the watch's heart rate needs no repair and Zwift has heart rate throughout."""
    for watch_path, zwift_path in reversed(RIDE_PAIRS):
        ride = read_zwift_fit(zwift_path)
        if sum(1 for s in ride.track.samples if s["heart_rate"]) < 0.99 * len(ride.track.samples):
            continue
        repair = HeartRateRepair(FitFile.from_file(str(watch_path)), ride.track)
        repair.run()
        if not repair.frozen_periods and not repair.unfixable_periods:
            return watch_path, zwift_path
    pytest.skip("no ride with good heart rate on both sides")


def freeze_heart_rate(watch_path, output_path, start_share=0.4, end_share=0.6):
    """Simulates the watch losing the HR strap: the value at start_share repeats until end_share."""
    fit = FitFile.from_file(str(watch_path))
    records = [r.message for r in fit.records if isinstance(r.message, RecordMessage)]
    first, last = int(len(records) * start_share), int(len(records) * end_share)
    frozen_value = records[first].heart_rate
    for msg in records[first:last]:
        set_field(msg, "heart_rate", frozen_value)
    fit.to_file(str(output_path), preserve=False)
    return records[first].timestamp // 1000, records[last - 1].timestamp // 1000


def test_frozen_heart_rate_is_replaced_with_zwift(tmp_path):
    watch_path, zwift_path = ride_with_good_heart_rate()
    frozen_path = tmp_path / "frozen.fit"
    start, end = freeze_heart_rate(watch_path, frozen_path)

    merge_quietly(watch_path, zwift_path, tmp_path / "original.fit")
    merge_quietly(frozen_path, zwift_path, tmp_path / "repaired.fit")
    zwift_hr = {int(r["timestamp"].timestamp()): r.get("heart_rate") for r in read_fit(zwift_path)["record"]}
    repaired = read_fit(tmp_path / "repaired.fit")

    for r in repaired["record"]:
        t = int(r["timestamp"].timestamp())
        if start <= t <= end:
            assert r["heart_rate"] == zwift_hr[t]

    original = read_fit(tmp_path / "original.fit")["session"][0]
    session = repaired["session"][0]
    assert session["avg_heart_rate"] == pytest.approx(original["avg_heart_rate"], abs=1)
    assert session["max_heart_rate"] == pytest.approx(original["max_heart_rate"], abs=1)
    original_zones = next(z for z in read_fit(tmp_path / "original.fit")["time_in_zone"] if z.get("reference_mesg") == "session")
    zones = next(z for z in repaired["time_in_zone"] if z.get("reference_mesg") == "session")
    for ours, expected in zip(zones["time_in_hr_zone"], original_zones["time_in_hr_zone"]):
        assert ours == pytest.approx(expected, abs=0.02 * session["total_elapsed_time"])
    assert zones["time_in_power_zone"] == original_zones["time_in_power_zone"], "power zones stay the watch's"


def test_good_heart_rate_is_left_alone():
    watch_path, zwift_path = ride_with_good_heart_rate()
    repair = HeartRateRepair(FitFile.from_file(str(watch_path)), read_zwift_fit(zwift_path).track)
    repair.run()
    assert repair.frozen_seconds == 0
