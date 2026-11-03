"""Rebuilds the watch laps so they still make sense after the merge.

The watch auto-laps on its own estimated distance, so after the merge those laps end at
random points of the real ride. They are replaced by:
- Zwift's laps when Zwift recorded more than one (the intervals of a Zwift workout), or
- auto-laps re-split on the real Zwift distance, using the watch's auto-lap distance.
Laps set with the lap button are kept as they are.

New laps are copies of the watch lap they overlap most, so fields that can't be
recalculated from records (e.g. Garmin-only lap fields) keep plausible values.
"""

import copy
import math
import statistics

from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.time_in_zone_message import TimeInZoneMessage
from fit_tool.profile.profile_type import LapTrigger

from lib.fit.stats import recompute_stats, recompute_zones, summary_window, window_between
from lib.fit.utils import insert_messages, secs, set_field, set_if_defined


class LapRebuilder:
    def __init__(self, garmin_fit, ride):
        self.fit = garmin_fit
        self.ride = ride
        self.session = next(r.message for r in garmin_fit.records if isinstance(r.message, SessionMessage))
        self.lap_records = sorted(
            (r for r in garmin_fit.records if isinstance(r.message, LapMessage)),
            key=lambda r: r.message.message_index,
        )
        self.start = secs(self.session.start_time)
        self.end = self.start + (self.session.total_elapsed_time or 0)
        self.mode, self.boundaries = self._plan()

    def describe(self):
        if self.mode == "zwift":
            return f"{len(self.boundaries)} laps from the Zwift workout"
        if self.mode == "distance":
            return f"{len(self.boundaries)} auto-laps every {self.lap_distance / 1000:g} km of Zwift distance"
        return "keeping the watch laps"

    def _plan(self):
        """Returns (mode, lap start times) or (None, None) to keep the watch laps."""
        zwift = [t for t in self.ride.lap_starts[1:] if self.start < t < self.end - 1]
        if zwift:
            return "zwift", [self.start] + zwift

        # Re-split only pure auto-lap rides; any manual lap means the user chose the laps
        laps = [r.message for r in self.lap_records]
        auto = [lap for lap in laps[:-1] if lap.lap_trigger == LapTrigger.DISTANCE.value]
        if not auto or len(auto) != len(laps) - 1:
            return None, None
        # Watch distance is still the watch's own at this point; its auto-lap distance is the typical lap
        self.lap_distance = round(statistics.median(lap.total_distance or 0 for lap in auto) / 100) * 100
        # Implausible lap distances (e.g. a file merged before) are left alone
        if not 100 <= self.lap_distance < self.ride.distance:
            return None, None

        boundaries = [self.start]
        base = self.ride.track.at(self.start)["distance"] if self.start > self.ride.track.start else 0.0
        k = 1
        for s in self.ride.track.samples:
            if self.start < s["t"] < self.end - 1 and s["distance"] - base >= k * self.lap_distance:
                boundaries.append(s["t"])
                k += 1
        return "distance", boundaries

    def run(self):
        if not self.mode:
            return

        lap_zone_records = {
            r.message.reference_index: r
            for r in self.fit.records
            if isinstance(r.message, TimeInZoneMessage) and r.message.reference_mesg == LapMessage.ID
        }
        shared_definitions = {}
        new_laps, new_zones = [], []

        ends = self.boundaries[1:] + [None]
        for index, (lap_start, next_start) in enumerate(zip(self.boundaries, ends)):
            lap_end = self.end if next_start is None else next_start
            source = self._most_overlapping_lap(lap_start, lap_end)
            lap = self._clone(source, shared_definitions)

            set_field(lap, "message_index", index)
            lap.start_time = lap_start * 1000
            lap.total_elapsed_time = lap_end - lap_start
            set_if_defined(lap, "total_timer_time", lap_end - lap_start)
            if next_start is None:
                trigger = LapTrigger.SESSION_END
            else:
                trigger = LapTrigger.DISTANCE if self.mode == "distance" else LapTrigger.MANUAL
            set_field(lap, "lap_trigger", trigger.value)

            window = window_between(self.fit, lap_start, math.ceil(lap_end))
            recompute_stats(lap, window)
            self._set_calories(lap)
            new_laps.append((source.header.local_id, lap))

            zone_source = lap_zone_records.get(source.message.message_index)
            if zone_source is not None:
                zones = self._clone(zone_source, shared_definitions)
                set_field(zones, "reference_index", index)
                recompute_zones(zones, window)
                new_zones.append((zone_source.header.local_id, zones))

        self._replace(new_laps, new_zones)
        set_if_defined(self.session, "num_laps", len(new_laps))
        set_if_defined(self.session, "first_lap_index", 0)

    def _most_overlapping_lap(self, t_start, t_end):
        def overlap(record):
            lap_start, lap_end = summary_window(record.message)
            return min(t_end, lap_end) - max(t_start, lap_start)
        return max(self.lap_records, key=overlap)

    @staticmethod
    def _clone(record, shared_definitions):
        """Copy of a record's message; copies with the same layout share one definition."""
        msg = copy.deepcopy(record.message)
        key = record.message.definition_message.to_bytes()
        msg.definition_message = shared_definitions.setdefault(key, msg.definition_message)
        return msg

    def _set_calories(self, lap):
        """Lap share of the watch's session calories, by work done."""
        work, calories = self.session.total_work, self.session.total_calories
        if work and calories is not None and lap.total_work is not None:
            set_if_defined(lap, "total_calories", round(calories * lap.total_work / work))

    def _replace(self, new_laps, new_zones):
        """Swaps the old lap and lap time-in-zone messages for the new ones, in the same place."""
        kept = []
        lap_pos = zone_pos = None
        for r in self.fit.records:
            msg = r.message
            if isinstance(msg, LapMessage):
                lap_pos = len(kept) if lap_pos is None else lap_pos
                continue
            if isinstance(msg, TimeInZoneMessage) and msg.reference_mesg == LapMessage.ID:
                zone_pos = len(kept) if zone_pos is None else zone_pos
                continue
            kept.append(r)

        # Insert at the later position first so the earlier one stays valid
        blocks = [(pos, msgs) for pos, msgs in ((lap_pos, new_laps), (zone_pos, new_zones)) if pos is not None]
        for pos, messages in sorted(blocks, key=lambda block: block[0], reverse=True):
            insert_messages(kept, pos, messages)
        self.fit.records = kept
