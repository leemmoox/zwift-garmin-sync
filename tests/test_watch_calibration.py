"""Our summary calculations must match what the watch itself writes.

Recomputes session and lap stats from the watch's own records (no merge) and compares
them with the watch's values, so changes to lib/fit/stats.py can't drift from Garmin's.
"""

import copy

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.time_in_zone_message import TimeInZoneMessage

from conftest import ride_params
from lib.fit.stats import recompute_stats, recompute_zones, summary_window, window_between

# Allowed difference per field. Max values can differ by 1 because the watch closes laps
# part-way through a second; one lap in the test data has cadence off by 2.
TOLERANCES = {
    "avg_power": 1,
    "max_power": 1,
    "normalized_power": 1,
    "avg_heart_rate": 1,
    "max_heart_rate": 1,
    "avg_cadence": 2,
    "max_cadence": 1,
}
POWER_FIELDS = {"avg_power", "max_power", "normalized_power"}
WORK_TOLERANCE_PCT = 0.5

# When the file's per-second power adds up to clearly less than the watch's own work, the
# watch saw power that wasn't written per second (sensor dropouts). Its NP/average then
# can't be reproduced from the file, so power fields aren't compared for that lap.
INCOMPLETE_POWER_PCT = 0.5


def summaries(fit):
    return [r.message for r in fit.records if isinstance(r.message, (SessionMessage, LapMessage))]


def label(msg):
    return f"{type(msg).__name__} {getattr(msg, 'message_index', '')}"


@pytest.mark.parametrize("pair", ride_params())
def test_stats_match_watch(pair):
    fit = FitFile.from_file(str(pair[0]))
    for original in summaries(fit):
        window = window_between(fit, *summary_window(original))
        ours = copy.deepcopy(original)
        recompute_stats(ours, window)

        per_second_work = sum(s.power or 0 for s in window.samples)
        incomplete = bool(original.total_work) and (
            (original.total_work - per_second_work) / original.total_work * 100 > INCOMPLETE_POWER_PCT
        )
        for field, tolerance in TOLERANCES.items():
            watch_value, our_value = getattr(original, field), getattr(ours, field)
            if watch_value is None or (incomplete and field in POWER_FIELDS):
                continue
            assert abs(our_value - watch_value) <= tolerance, (
                f"{label(original)}: {field} watch={watch_value} ours={our_value}"
            )

        if original.total_work:
            error_pct = abs(ours.total_work - original.total_work) / original.total_work * 100
            assert error_pct <= WORK_TOLERANCE_PCT, (
                f"{label(original)}: total_work watch={original.total_work} ours={ours.total_work}"
            )


@pytest.mark.parametrize("pair", ride_params())
def test_hr_zones_match_watch(pair):
    fit = FitFile.from_file(str(pair[0]))
    session = next(m for m in summaries(fit) if isinstance(m, SessionMessage))
    zones = next(r.message for r in fit.records
                 if isinstance(r.message, TimeInZoneMessage) and r.message.reference_mesg == SessionMessage.ID)
    ours = copy.deepcopy(zones)
    recompute_zones(ours, window_between(fit, *summary_window(session)))
    for watch_seconds, our_seconds in zip(zones.time_in_hr_zone, ours.time_in_hr_zone):
        assert abs(our_seconds - watch_seconds) <= 2
