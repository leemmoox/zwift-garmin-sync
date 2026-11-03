"""Aligns a Garmin FIT recording to the Zwift ride window.

Handles a watch that was started late / stopped late (or early) compared to Zwift:
records outside the Zwift ride are removed, missing seconds are filled from Zwift data,
and the affected lap/session summaries are recalculated from the resulting records.
"""

from fit_tool.data_message import DataMessage
from fit_tool.definition_message import DefinitionMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.time_in_zone_message import TimeInZoneMessage

from lib.fit.stats import recompute_stats, recompute_zones, summary_window, window_between
from lib.fit.utils import (
    FIT_EPOCH_OFFSET,
    SEMICIRCLES_TO_DEG,
    add_field_definitions,
    insert_messages,
    secs,
    set_field,
    set_if_defined,
    valid_value,
)

# Undocumented Garmin messages: 233 is written right after every record (no timestamp),
# 104 is a periodic sample with its own timestamp (field 253).
GARMIN_RECORD_COMPANION_ID = 233
GARMIN_TIMED_SAMPLE_ID = 104

FILLED_RECORD_FIELDS = [
    "timestamp", "distance", "enhanced_speed", "enhanced_altitude", "position_lat",
    "position_long", "power", "heart_rate", "cadence", "accumulated_power",
]


def ride_window(garmin_fit):
    """First and last record timestamps (seconds) of a Garmin FitFile."""
    times = [
        secs(r.message.timestamp)
        for r in garmin_fit.records
        if isinstance(r.message, RecordMessage) and r.message.timestamp is not None
    ]
    if not times:
        raise ValueError("Garmin FIT file contains no record data.")
    return times[0], times[-1]


class Aligner:
    def __init__(self, garmin_fit, track, tolerance_sec):
        self.fit = garmin_fit
        self.track = track
        self.g_start, self.g_end = ride_window(garmin_fit)

        # Small differences are left exactly as the watch recorded them
        self.new_start = track.start if abs(self.g_start - track.start) > tolerance_sec else self.g_start
        self.new_end = track.end if abs(self.g_end - track.end) > tolerance_sec else self.g_end
        self.delta_start = self.g_start - self.new_start  # > 0: seconds added at start, < 0: trimmed
        self.delta_end = self.new_end - self.g_end  # > 0: seconds added at end, < 0: trimmed
        self.filled = set()  # ids of records built from Zwift data

        self.edges = self._summary_edges()

    @property
    def changed(self):
        return self.delta_start != 0 or self.delta_end != 0

    def describe(self):
        def part(delta, where):
            if delta > 0:
                return f"added {delta} s at the {where} from Zwift"
            if delta < 0:
                return f"cut {-delta} s at the {where}"
            return None

        parts = [p for p in (part(self.delta_start, "start"), part(self.delta_end, "end")) if p]
        return ", ".join(parts) if parts else "no changes needed"

    def run(self):
        if not self.changed:
            return
        self._trim()
        self._fill()
        self._renumber_laps()
        self._update_summaries()

    # --- records ---

    def _trim(self):
        kept = []
        dropping = False
        self.removed_lap_indexes = set()
        for record in self.fit.records:
            msg = record.message
            if isinstance(msg, RecordMessage):
                t = secs(msg.timestamp)
                dropping = t < self.new_start or t > self.new_end
                if dropping:
                    continue
            elif isinstance(msg, LapMessage):
                lap_start, lap_end = self.edges[id(msg)]
                if lap_start >= self.new_end or lap_end <= self.new_start:
                    self.removed_lap_indexes.add(msg.message_index)
                    continue
            elif isinstance(msg, DataMessage) and msg.global_id == GARMIN_RECORD_COMPANION_ID:
                if dropping:
                    continue
            elif isinstance(msg, DataMessage) and msg.global_id == GARMIN_TIMED_SAMPLE_ID:
                ts_field = msg.get_field(253)
                ts = ts_field.get_value(0) if ts_field else None
                if ts is not None and not self.new_start <= ts + FIT_EPOCH_OFFSET <= self.new_end:
                    continue
            elif not isinstance(msg, DefinitionMessage):
                dropping = False
            kept.append(record)

        # Zone summaries of removed laps go too
        self.fit.records = [
            r for r in kept
            if not (
                isinstance(r.message, TimeInZoneMessage)
                and r.message.reference_mesg == LapMessage.ID
                and r.message.reference_index in self.removed_lap_indexes
            )
        ]

    def _fill(self):
        records = self.fit.records
        record_idx = [i for i, r in enumerate(records) if isinstance(r.message, RecordMessage)]

        if self.delta_end > 0:
            samples = [s for s in self.track.samples if self.g_end < s["t"] <= self.new_end]
            last = record_idx[-1]
            # Insert after the record's companion message, if present
            pos = last + 1
            if pos < len(records) and getattr(records[pos].message, "global_id", None) == GARMIN_RECORD_COMPANION_ID:
                pos += 1
            self._insert_block(pos, records[last].header.local_id, samples)

        if self.delta_start > 0:
            samples = [s for s in self.track.samples if self.new_start <= s["t"] < self.g_start]
            first = record_idx[0]
            self._insert_block(first, records[first].header.local_id, samples)

        self._recompute_accumulated_power()

    def _insert_block(self, pos, local_id, samples):
        """Inserts records built from Zwift samples at pos."""
        if not samples:
            return
        definition = DefinitionMessage(global_id=RecordMessage.ID, local_id=local_id)
        add_field_definitions(definition, RecordMessage, FILLED_RECORD_FIELDS)

        messages = []
        for s in samples:
            msg = RecordMessage(local_id=local_id)
            msg.set_definition_message(definition)
            set_field(msg, "timestamp", s["t"] * 1000)
            set_field(msg, "distance", s["distance"])
            set_field(msg, "enhanced_speed", s["speed"])
            if s["altitude"] is not None:
                set_field(msg, "enhanced_altitude", s["altitude"])
            if s["lat"] is not None and s["long"] is not None:
                set_field(msg, "position_lat", s["lat"] * SEMICIRCLES_TO_DEG)
                set_field(msg, "position_long", s["long"] * SEMICIRCLES_TO_DEG)
            for name in ("power", "heart_rate", "cadence"):
                if s[name] is not None:
                    set_field(msg, name, s[name])
            messages.append((local_id, msg))
            self.filled.add(id(msg))

        insert_messages(self.fit.records, pos, messages)

    def _recompute_accumulated_power(self):
        """Rebuilds the accumulated-power counter so it starts at 0 at the new start.

        Increments between watch records come from the power meter's own counter (more
        precise than 1 s power); seconds filled from Zwift add their per-second power.
        """
        total = 0
        previous = None  # watch counter of the previous record, if that was a watch record
        for r in self.fit.records:
            msg = r.message
            if not isinstance(msg, RecordMessage):
                continue
            counter = valid_value(msg, "accumulated_power")
            from_watch = id(msg) not in self.filled
            if from_watch and counter is not None and previous is not None:
                total += counter - previous
            else:
                total += valid_value(msg, "power") or 0
            previous = counter if from_watch else None
            set_if_defined(msg, "accumulated_power", total)

    # --- laps & summaries ---

    def _renumber_laps(self):
        if not self.removed_lap_indexes:
            return
        laps = [r.message for r in self.fit.records if isinstance(r.message, LapMessage)]
        mapping = {}
        for new_index, lap in enumerate(sorted(laps, key=lambda m: m.message_index)):
            mapping[lap.message_index] = new_index
            set_field(lap, "message_index", new_index)
        for r in self.fit.records:
            msg = r.message
            if isinstance(msg, TimeInZoneMessage) and msg.reference_mesg == LapMessage.ID:
                set_field(msg, "reference_index", mapping[msg.reference_index])
            elif isinstance(msg, SessionMessage):
                set_if_defined(msg, "num_laps", len(laps))
                set_if_defined(msg, "first_lap_index", 0)

    def _update_summaries(self):
        old_start_ms = self.g_start * 1000
        new_start_ms = self.new_start * 1000
        shift = self.new_start - self.g_start

        laps = sorted(
            (r.message for r in self.fit.records if isinstance(r.message, LapMessage)),
            key=lambda m: m.message_index,
        )
        first_lap, last_lap = laps[0], laps[-1]
        changed_laps = set()
        if self.delta_start:
            changed_laps.add(first_lap.message_index)
        if self.delta_end:
            changed_laps.add(last_lap.message_index)

        session = None
        for r in self.fit.records:
            msg = r.message
            # Garmin stamps summary messages (and the timer start event) with the activity start
            if isinstance(msg, (SessionMessage, LapMessage, TimeInZoneMessage, ActivityMessage, EventMessage)):
                if msg.timestamp == old_start_ms:
                    msg.timestamp = new_start_ms
                    if isinstance(msg, ActivityMessage) and msg.local_timestamp is not None:
                        set_field(msg, "local_timestamp", msg.local_timestamp + shift)
                elif isinstance(msg, EventMessage) and msg.timestamp is not None and secs(msg.timestamp) >= self.g_end:
                    msg.timestamp = self.new_end * 1000

            if isinstance(msg, ActivityMessage) and msg.total_timer_time is not None:
                msg.total_timer_time = max(msg.total_timer_time + self.delta_start + self.delta_end, 0)
            elif isinstance(msg, SessionMessage):
                session = msg
                self._fit_to_window(msg, True, True)
            elif isinstance(msg, LapMessage):
                self._fit_to_window(msg, msg is first_lap, msg is last_lap)

        threshold = session.threshold_power if session else None
        for msg in [session] + laps:
            if msg is None or (isinstance(msg, LapMessage) and msg.message_index not in changed_laps):
                continue
            old_work, old_calories = msg.total_work, msg.total_calories
            recompute_stats(msg, window_between(self.fit, *summary_window(msg)), threshold)
            # Calories scale with the work done (watch's own estimate for the recorded part)
            if old_work and old_calories is not None and msg.total_work is not None:
                set_if_defined(msg, "total_calories", round(old_calories * msg.total_work / old_work))

        for r in self.fit.records:
            msg = r.message
            if not isinstance(msg, TimeInZoneMessage):
                continue
            if msg.reference_mesg == SessionMessage.ID:
                target = session
            elif msg.reference_mesg == LapMessage.ID and msg.reference_index in changed_laps:
                target = laps[msg.reference_index]
            else:
                continue
            recompute_zones(msg, window_between(self.fit, *summary_window(target)))

    def _summary_edges(self):
        """Exact (start, end) times of the session and each lap, by id of the message.

        The watch stores start times in whole seconds but durations to the millisecond, and
        keeps the lap durations adding up to the session duration. So the exact start of a
        lap is the session start plus all previous lap durations.
        """
        edges = {}
        session = next((r.message for r in self.fit.records if isinstance(r.message, SessionMessage)), None)
        if session is None:
            return edges
        start = session.start_time / 1000
        edges[id(session)] = (start, start + (session.total_elapsed_time or 0))
        laps = sorted((r.message for r in self.fit.records if isinstance(r.message, LapMessage)),
                      key=lambda m: m.message_index)
        for lap in laps:
            end = start + (lap.total_elapsed_time or 0)
            edges[id(lap)] = (start, end)
            start = end
        # The session ends this much after its last record; the new end keeps the same offset
        self.end_offset = edges[id(session)][1] - self.g_end
        return edges

    def _fit_to_window(self, msg, is_first, is_last):
        """Moves the start of the first lap/session to new_start and the end of the last one to new_end."""
        start, end = self.edges[id(msg)]
        new_start, new_end = start, end
        if is_first and self.delta_start:
            new_start = self.new_start
            msg.start_time = self.new_start * 1000
        if is_last and self.delta_end:
            new_end = self.new_end + self.end_offset
        delta = (new_end - new_start) - (end - start)
        if not delta:
            return
        for name in ("total_elapsed_time", "total_timer_time"):
            value = getattr(msg, name)
            if value is not None:
                setattr(msg, name, max(value + delta, 0))
