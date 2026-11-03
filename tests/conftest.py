"""Test fixtures: real ride pairs from the local FIT cache.

Rides are personal data and never committed. Tests use the files the app downloads into
fit/ (gYYYYMMDD-HHMM.fit from the watch, zYYYYMMDD-HHMM.fit from Zwift), or another
folder set in ZG_TEST_FIT_DIR. Tests that need rides are skipped when there are none.
ZG_TEST_MAX_RIDES limits the run to the newest N rides (each ride takes ~15 s).
"""

import contextlib
import io
import os
from datetime import datetime
from pathlib import Path

import fitdecode
import pytest

from lib.fit import ActivityMerger

ROOT = Path(__file__).resolve().parents[1]
FIT_DIR = Path(os.getenv("ZG_TEST_FIT_DIR", ROOT / "fit"))
MAX_PAIR_DIFF_SEC = 15 * 60


def _start(path):
    return datetime.strptime(path.stem[1:], "%Y%m%d-%H%M")


def _already_merged(path):
    """Watch files recorded indoors say indoor_cycling; a merged file says virtual_activity."""
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == "session":
                return frame.get_value("sub_sport", fallback=None) == "virtual_activity"
    return False


def find_ride_pairs():
    """(watch file, Zwift file) pairs whose file names start within 15 minutes of each other.

    Watch files that were already merged (e.g. by an older version) aren't valid test input.
    """
    watch = [g for g in sorted(FIT_DIR.glob("g*.fit")) if not _already_merged(g)]
    zwift = sorted(FIT_DIR.glob("z*.fit"))
    pairs = []
    for g in watch:
        if not zwift:
            break
        z = min(zwift, key=lambda z: abs((_start(z) - _start(g)).total_seconds()))
        if abs((_start(z) - _start(g)).total_seconds()) <= MAX_PAIR_DIFF_SEC:
            pairs.append((g, z))
    return pairs


RIDE_PAIRS = find_ride_pairs()
if os.getenv("ZG_TEST_MAX_RIDES"):
    RIDE_PAIRS = RIDE_PAIRS[-int(os.environ["ZG_TEST_MAX_RIDES"]):]


def ride_params():
    if not RIDE_PAIRS:
        return [pytest.param(None, marks=pytest.mark.skip(reason=f"no ride pairs in {FIT_DIR}"))]
    return [pytest.param(pair, id=pair[0].stem) for pair in RIDE_PAIRS]


def merge_quietly(garmin_path, zwift_path, output_path):
    with contextlib.redirect_stdout(io.StringIO()):
        merger = ActivityMerger(str(garmin_path), str(zwift_path), str(output_path))
        merger.process()
    return merger


@pytest.fixture(scope="session")
def merged_rides(tmp_path_factory):
    """Merges every ride pair once per test run; maps watch file -> merged file (or the exception)."""
    out_dir = tmp_path_factory.mktemp("merged")
    results = {}

    def get(pair):
        garmin_path, zwift_path = pair
        if garmin_path not in results:
            output = out_dir / garmin_path.name
            try:
                merge_quietly(garmin_path, zwift_path, output)
                results[garmin_path] = output
            except Exception as exc:  # reported by the test that asked for it
                results[garmin_path] = exc
        result = results[garmin_path]
        if isinstance(result, Exception):
            raise result
        return result

    return get
