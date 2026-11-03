"""Recalculates lap/session summaries from per-second records, the same way the watch does.

Calibrated against Forerunner 965 files (see tests/test_watch_calibration.py):
- work comes from the power meter's accumulated-power counter (sampled faster than 1 s),
  falling back to the sum of per-second power when the watch didn't record it,
- average power is work / number of records, rounded,
- normalized power uses the 30 s rolling average of the whole activity (it does not restart
  at a lap), from the 30th record on,
- average cadence excludes zeros and is truncated; average HR and NP are rounded,
- time in zones is time, so a record followed by a recording gap counts for the whole gap,
  and a value exactly on a zone boundary counts in the zone above it.
"""

import math
from collections import deque
from typing import NamedTuple

from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage

from lib.fit.utils import secs, set_if_defined, valid_value


class Sample(NamedTuple):
    t: int
    power: int | None
    heart_rate: int | None
    cadence: int | None
    accumulated_power: int | None


class Window(NamedTuple):
    samples: list  # Sample per record in the window
    accumulated_before: int  # accumulated-power counter at the last record before the window
    rolling_power: list  # activity-wide 30 s rolling average power at each record in the window

    @property
    def work(self):
        counters = [s.accumulated_power for s in self.samples if s.accumulated_power is not None]
        if counters:
            return counters[-1] - self.accumulated_before
        return sum(s.power or 0 for s in self.samples)

    def durations(self):
        """Seconds each sample represents: until the next record, 1 s for the last one."""
        times = [s.t for s in self.samples]
        return [max(b - a, 1) for a, b in zip(times, times[1:])] + ([1] if times else [])


def summary_window(msg):
    """(start, end) in seconds of a lap/session message; Garmin's own timestamp field is the start."""
    start = secs(msg.start_time)
    return start, start + math.ceil(msg.total_elapsed_time or 0)


def window_between(garmin_fit, t_start, t_end):
    """Records in [t_start, t_end) as a Window."""
    samples = []
    rolling = []
    before = 0
    last_30 = deque(maxlen=30)
    for r in garmin_fit.records:
        msg = r.message
        if not isinstance(msg, RecordMessage):
            continue
        t = secs(msg.timestamp)
        if t >= t_end:
            break
        power = valid_value(msg, "power")
        counter = valid_value(msg, "accumulated_power")
        last_30.append(power or 0)
        if t < t_start:
            if counter is not None:
                before = counter
            continue
        samples.append(Sample(t, power, valid_value(msg, "heart_rate"), valid_value(msg, "cadence"), counter))
        if len(last_30) == 30:
            rolling.append(sum(last_30) / 30)
    return Window(samples, before, rolling)


def recompute_stats(msg, window, threshold_power=None):
    """Sets power/HR/cadence/work/cycle summaries, plus TSS/IF for a session when FTP is known."""
    samples = window.samples
    if not samples:
        return
    power = [s.power or 0 for s in samples]
    hr = [s.heart_rate for s in samples if s.heart_rate]
    cadence = [s.cadence for s in samples if s.cadence]

    work = window.work
    # Very short windows at the activity start have no full 30 s average yet
    rolling = window.rolling_power or [sum(power) / len(power)]
    normalized = (sum(x**4 for x in rolling) / len(rolling)) ** 0.25

    set_if_defined(msg, "avg_power", round(work / len(samples)))
    set_if_defined(msg, "max_power", max(power))
    set_if_defined(msg, "normalized_power", round(normalized))
    set_if_defined(msg, "total_work", work)
    if hr:
        set_if_defined(msg, "avg_heart_rate", round(sum(hr) / len(hr)))
        set_if_defined(msg, "max_heart_rate", max(hr))
    if cadence:
        set_if_defined(msg, "avg_cadence", int(sum(cadence) / len(cadence)))
        set_if_defined(msg, "max_cadence", max(cadence))
        # 1 s records: revolutions = rpm / 60 per second
        set_if_defined(msg, "total_cycles", round(sum(cadence) / 60))

    if threshold_power and isinstance(msg, SessionMessage):
        intensity = round(normalized) / threshold_power
        duration = msg.total_timer_time or len(power)
        set_if_defined(msg, "intensity_factor", intensity)
        set_if_defined(msg, "training_stress_score", duration * intensity**2 / 3600 * 100)


def recompute_zones(msg, window):
    """Sets time in HR and power zones of a time_in_zone message from the window."""
    durations = window.durations()

    def fill(field_name, boundaries, values):
        field = msg.get_field_by_name(field_name)
        if not field or not boundaries or field.length == 0:
            return
        seconds = [0] * field.length
        for value, duration in values:
            zone = next((i for i, b in enumerate(boundaries) if value < b), len(boundaries))
            seconds[min(zone, field.length - 1)] += duration
        for i, total in enumerate(seconds):
            field.set_value(i, float(total))

    fill("time_in_hr_zone", msg.hr_zone_high_boundary,
         [(s.heart_rate, d) for s, d in zip(window.samples, durations) if s.heart_rate])
    fill("time_in_power_zone", msg.power_zone_high_boundary,
         [(s.power or 0, d) for s, d in zip(window.samples, durations)])
