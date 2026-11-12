"""Repairs the watch's heart rate with Zwift's when the watch lost the HR sensor.

When the watch loses the HR strap mid-ride it doesn't always stop recording heart rate: it
can keep writing the last value for the rest of the ride. Zwift, connected to the same
strap on its own, usually still has the real values. So:
- a value repeated for at least FROZEN_MIN_SEC while Zwift's heart rate changes by at least
  ZWIFT_MIN_RANGE_BPM is frozen, and is replaced with Zwift's heart rate;
- seconds without heart rate on the watch get Zwift's.
Real steady heart rate can stay on one value for about a minute, but Zwift then shows the
same (within 1 bpm), so it is left alone. If Zwift lost the strap too, nothing is changed.

Average/max heart rate and time in HR zones of the session and affected laps are then
recalculated. Training Effect and training load are calculated by the watch during the
ride and can't be recalculated.
"""

from itertools import groupby

from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.time_in_zone_message import TimeInZoneMessage

from lib.fit.stats import recompute_heart_rate, recompute_zones, summary_window, window_between
from lib.fit.utils import secs, set_field, valid_value

FROZEN_MIN_SEC = 30
ZWIFT_MIN_RANGE_BPM = 5
UNFIXABLE_WARN_SEC = 5 * 60


class HeartRateRepair:
    def __init__(self, garmin_fit, track):
        self.fit = garmin_fit
        self.zwift_hr = {s["t"]: s["heart_rate"] for s in track.samples if s["heart_rate"]}
        self.frozen_periods = []  # (start, end, frozen value) replaced with Zwift's heart rate
        self.unfixable_periods = []  # frozen, but Zwift has no heart rate there either
        self.frozen_seconds = 0
        self.missing_seconds = 0
        self.changed_times = []

    def describe(self):
        parts = []
        for start, end, value in self.frozen_periods:
            parts.append(f"frozen at {value} bpm for {(end - start + 1) / 60:.0f} min, replaced with Zwift's")
        if self.missing_seconds:
            parts.append(f"{self.missing_seconds} s without heart rate filled from Zwift")
        for start, end, value in self.unfixable_periods:
            parts.append(f"⚠️ frozen at {value} bpm for {(end - start + 1) / 60:.0f} min, "
                         f"but Zwift has no heart rate there either, left as recorded")
        return "; ".join(parts) if parts else "watch heart rate is fine"

    def run(self):
        records = [
            (r.message, secs(r.message.timestamp), valid_value(r.message, "heart_rate"))
            for r in self.fit.records
            if isinstance(r.message, RecordMessage)
        ]
        replacements = {}

        for value, group in groupby(records, key=lambda record: record[2]):
            group = list(group)
            if value is None:
                for msg, t, _ in group:
                    if t in self.zwift_hr:
                        replacements[id(msg)] = (msg, t)
                        self.missing_seconds += 1
                continue
            first, last = group[0][1], group[-1][1]
            if last - first + 1 < FROZEN_MIN_SEC:
                continue
            zwift = [self.zwift_hr[t] for _, t, _ in group if t in self.zwift_hr]
            if len(zwift) < len(group) / 2:
                # Steady heart rate doesn't last this long; without Zwift data it can't be fixed
                if last - first + 1 >= UNFIXABLE_WARN_SEC:
                    self.unfixable_periods.append((first, last, value))
                continue
            if max(zwift) - min(zwift) < ZWIFT_MIN_RANGE_BPM:
                continue
            self.frozen_periods.append((first, last, value))
            for msg, t, _ in group:
                if t in self.zwift_hr:
                    replacements[id(msg)] = (msg, t)
                    self.frozen_seconds += 1

        for msg, t in replacements.values():
            set_field(msg, "heart_rate", self.zwift_hr[t])
            self.changed_times.append(t)
        if self.changed_times:
            self._update_summaries()

    def _touches_changes(self, msg):
        start, end = summary_window(msg)
        return any(start <= t < end for t in self.changed_times)

    def _update_summaries(self):
        laps, session = {}, None
        for r in self.fit.records:
            msg = r.message
            if isinstance(msg, (SessionMessage, LapMessage)) and self._touches_changes(msg):
                recompute_heart_rate(msg, window_between(self.fit, *summary_window(msg)))
            if isinstance(msg, LapMessage):
                laps[msg.message_index] = msg
            elif isinstance(msg, SessionMessage):
                session = msg

        for r in self.fit.records:
            msg = r.message
            if not isinstance(msg, TimeInZoneMessage):
                continue
            target = session if msg.reference_mesg == SessionMessage.ID else laps.get(msg.reference_index)
            if target is not None and self._touches_changes(target):
                # Power zones stay as the watch calculated them
                recompute_zones(msg, window_between(self.fit, *summary_window(target)), power=False)
