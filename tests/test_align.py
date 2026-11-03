"""Watch started late / stopped early or late: cut and fill must reproduce the real ride."""

import pytest
from fit_tool.fit_file import FitFile

from conftest import RIDE_PAIRS, merge_quietly, ride_params
from fitdata import read_fit
from lib.fit.align import Aligner
from lib.fit.zwift_track import ZwiftTrack, read_zwift_fit

# Share of the ride the simulated watch misses; together below 20% so the merge is allowed
CUT_START_SHARE = 0.07
CUT_END_SHARE = 0.08


def longest_pair():
    if not RIDE_PAIRS:
        pytest.skip("no ride pairs")
    return max(RIDE_PAIRS, key=lambda pair: len(read_zwift_fit(pair[1]).track.samples))


def cut_watch_file(watch_path, zwift_path, output_path):
    """Simulates a watch started late and stopped early (by CUT_START/END_SHARE of the ride)."""
    track = read_zwift_fit(zwift_path).track
    duration = track.end - track.start
    first, last = track.start + duration * CUT_START_SHARE, track.end - duration * CUT_END_SHARE
    short = ZwiftTrack([s for s in track.samples if first <= s["t"] <= last])
    fit = FitFile.from_file(str(watch_path))
    Aligner(fit, short, tolerance_sec=10).run()
    fit.to_file(str(output_path), preserve=False)


def test_cut_then_refill_matches_full_recording(tmp_path):
    watch_path, zwift_path = longest_pair()
    cut_path = tmp_path / "cut.fit"
    cut_watch_file(watch_path, zwift_path, cut_path)

    merge_quietly(watch_path, zwift_path, tmp_path / "full.fit")
    merge_quietly(cut_path, zwift_path, tmp_path / "refilled.fit")
    full = read_fit(tmp_path / "full.fit")["session"][0]
    refilled = read_fit(tmp_path / "refilled.fit")["session"][0]

    # The refilled file starts with Zwift; the full recording keeps the watch's own start (within 10 s)
    zwift_start = read_zwift_fit(zwift_path).track.start
    assert refilled["start_time"].timestamp() == zwift_start
    assert refilled["total_elapsed_time"] == pytest.approx(full["total_elapsed_time"], abs=10)
    assert refilled["total_distance"] == pytest.approx(full["total_distance"], abs=1)
    # Filled seconds come from Zwift's power/HR (same sensors), so totals stay close
    assert refilled["total_work"] == pytest.approx(full["total_work"], rel=0.01)
    assert refilled["avg_power"] == pytest.approx(full["avg_power"], abs=2)
    assert refilled["normalized_power"] == pytest.approx(full["normalized_power"], abs=2)
    assert refilled["avg_heart_rate"] == pytest.approx(full["avg_heart_rate"], abs=1)


def test_cut_file_is_consistent(tmp_path):
    watch_path, zwift_path = longest_pair()
    cut_path = tmp_path / "cut.fit"
    cut_watch_file(watch_path, zwift_path, cut_path)
    cut = read_fit(cut_path)
    session, records = cut["session"][0], cut["record"]

    assert records[0]["timestamp"] == session["start_time"]
    assert session["total_elapsed_time"] == pytest.approx(
        (records[-1]["timestamp"] - records[0]["timestamp"]).total_seconds(), abs=2)
    counters = [r["accumulated_power"] for r in records if "accumulated_power" in r]
    assert counters == sorted(counters), "accumulated power must never decrease"
    assert counters[-1] == pytest.approx(session["total_work"], rel=0.005)
    assert sum(lap["total_elapsed_time"] for lap in cut["lap"]) == pytest.approx(session["total_elapsed_time"], abs=1)


def test_unrelated_activity_is_refused(tmp_path):
    """A watch file that doesn't cover the Zwift ride must never be merged (and deleted on Garmin)."""
    if len(RIDE_PAIRS) < 2:
        pytest.skip("needs two ride pairs")
    (watch_a, _), (_, zwift_b) = RIDE_PAIRS[0], RIDE_PAIRS[1]
    with pytest.raises(ValueError, match="do not match"):
        merge_quietly(watch_a, zwift_b, tmp_path / "out.fit")


@pytest.mark.parametrize("pair", ride_params())
def test_unmodified_watch_file_reencodes_identically(pair):
    """Guards the in-place editing approach against fit_tool changes: no edits, no byte changes."""
    watch_path = pair[0]
    assert FitFile.from_file(str(watch_path)).to_bytes(preserve=False) == watch_path.read_bytes()
