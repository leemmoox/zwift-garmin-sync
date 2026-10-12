"""Aligns a Garmin FIT recording to the Zwift ride window.

Handles a watch that was started late / stopped late (or early) compared to Zwift:
records outside the Zwift ride are removed, missing seconds are filled from Zwift data,
and the affected lap/session summaries are recalculated from the resulting records.
"""

import math

from fit_tool.data_message import DataMessage
from fit_tool.definition_message import DefinitionMessage
from fit_tool.field_definition import FieldDefinition
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.time_in_zone_message import TimeInZoneMessage
from fit_tool.record import Record, RecordHeader

FIT_EPOCH_OFFSET = 631065600  # FIT timestamps count from 1989-12-31 00:00 UTC
SEMICIRCLES_TO_DEG = 180.0 / 2**31

# Undocumented Garmin messages: 233 is written right after every record (no timestamp),
# 104 is a periodic sample with its own timestamp (field 253).
GARMIN_RECORD_COMPANION_ID = 233
GARMIN_TIMED_SAMPLE_ID = 104

FILLED_RECORD_FIELDS = [
    "timestamp", "distance", "enhanced_speed", "enhanced_altitude", "position_lat",
    "position_long", "power", "heart_rate", "cadence", "accumulated_power",
]


def _secs(ms):
    return ms // 1000


def _set(msg, name, value):
    field = msg.get_field_by_name(name)
    field.growable = True
    field.set_value(0, value)


def _set_if_defined(msg, name, value):
    """Sets a field only if the watch wrote it (present in the message definition)."""
    field = msg.get_field_by_name(name)
    if field and msg.definition_message.get_field_definition(field.field_id) is not None:
        field.growable = True
        field.set_value(0, value)


def _valid(msg, name):
    """Field value, or None when missing or holding the FIT 'invalid' marker (e.g. 255 for no HR)."""
    field = msg.get_field_by_name(name)
    if not field or not field.encoded_values:
        return None
    raw = field.encoded_values[0]
    if raw is None or raw == field.base_type.invalid_raw_value():
        return None
    return field.get_value(0)


def _record_header(local_id, is_definition):
    return RecordHeader(is_definition=is_definition, local_id=local_id)


def ride_window(garmin_fit):
    """First and last record timestamps (seconds) of a Garmin FitFile."""
    times = [
        _secs(r.message.timestamp)
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
                t = _secs(msg.timestamp)
                dropping = t < self.new_start or t > self.new_end
                if dropping:
                    continue
            elif isinstance(msg, LapMessage):
                lap_start = _secs(msg.start_time)
                lap_end = lap_start + (msg.total_elapsed_time or 0)
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
        """Inserts Zwift-based records at pos, using local_id and restoring its previous definition afterwards."""
        if not samples:
            return
        records = self.fit.records

        previous_def = None
        for r in records[:pos]:
            if isinstance(r.message, DefinitionMessage) and r.header.local_id == local_id:
                previous_def = r.message

        proto = RecordMessage()
        definition = DefinitionMessage(global_id=RecordMessage.ID, local_id=local_id)
        for name in FILLED_RECORD_FIELDS:
            field = proto.get_field_by_name(name)
            definition.add_field_definition(FieldDefinition(field.field_id, field.base_type.size, field.base_type))

        block = [Record(_record_header(local_id, True), definition, dirty=True)]
        for s in samples:
            msg = RecordMessage(local_id=local_id)
            msg.set_definition_message(definition)
            _set(msg, "timestamp", s["t"] * 1000)
            _set(msg, "distance", s["distance"])
            _set(msg, "enhanced_speed", s["speed"])
            if s["altitude"] is not None:
                _set(msg, "enhanced_altitude", s["altitude"])
            if s["lat"] is not None and s["long"] is not None:
                _set(msg, "position_lat", s["lat"] * SEMICIRCLES_TO_DEG)
                _set(msg, "position_long", s["long"] * SEMICIRCLES_TO_DEG)
            for name in ("power", "heart_rate", "cadence"):
                if s[name] is not None:
                    _set(msg, name, s[name])
            block.append(Record(_record_header(local_id, False), msg, dirty=True))

        if previous_def is not None:
            block.append(Record(_record_header(local_id, True), previous_def, dirty=True))

        records[pos:pos] = block

    def _recompute_accumulated_power(self):
        total = 0
        for r in self.fit.records:
            msg = r.message
            if not isinstance(msg, RecordMessage):
                continue
            total += _valid(msg, "power") or 0
            _set_if_defined(msg, "accumulated_power", total)

    # --- laps & summaries ---

    def _renumber_laps(self):
        if not self.removed_lap_indexes:
            return
        laps = [r.message for r in self.fit.records if isinstance(r.message, LapMessage)]
        mapping = {}
        for new_index, lap in enumerate(sorted(laps, key=lambda m: m.message_index)):
            mapping[lap.message_index] = new_index
            _set(lap, "message_index", new_index)
        for r in self.fit.records:
            msg = r.message
            if isinstance(msg, TimeInZoneMessage) and msg.reference_mesg == LapMessage.ID:
                _set(msg, "reference_index", mapping[msg.reference_index])
            elif isinstance(msg, SessionMessage):
                _set_if_defined(msg, "num_laps", len(laps))
                _set_if_defined(msg, "first_lap_index", 0)

    def _samples(self, t_start, t_end):
        """(power, heart_rate, cadence) of final records in [t_start, t_end)."""
        out = []
        for r in self.fit.records:
            msg = r.message
            if isinstance(msg, RecordMessage) and t_start <= _secs(msg.timestamp) < t_end:
                out.append((_valid(msg, "power"), _valid(msg, "heart_rate"), _valid(msg, "cadence")))
        return out

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
                        _set(msg, "local_timestamp", msg.local_timestamp + shift)
                elif isinstance(msg, EventMessage) and msg.timestamp is not None and _secs(msg.timestamp) >= self.g_end:
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
            t_start = _secs(msg.start_time)
            t_end = t_start + math.ceil(msg.total_elapsed_time or 0)
            self._recompute_stats(msg, self._samples(t_start, t_end), threshold)

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
            t_start = _secs(target.start_time)
            t_end = t_start + math.ceil(target.total_elapsed_time or 0)
            self._recompute_zones(msg, self._samples(t_start, t_end))

    def _fit_to_window(self, msg, is_first, is_last):
        """Moves the start of the first lap/session to new_start and the end of the last one to new_end."""
        start = _secs(msg.start_time)
        end = start + int(msg.total_elapsed_time or 0)
        # Seconds gained (+) or lost (-) at each edge, measured against the recorded data
        delta = 0
        if is_first:
            delta += max(start, self.g_start) - self.new_start
            msg.start_time = self.new_start * 1000
        if is_last:
            delta += self.new_end - min(end, self.g_end)
        if not delta:
            return
        for name in ("total_elapsed_time", "total_timer_time"):
            value = getattr(msg, name)
            if value is not None:
                setattr(msg, name, max(value + delta, 0))

    @staticmethod
    def _recompute_stats(msg, samples, threshold_power):
        """Recalculates power/HR/cadence summaries the same way the watch does."""
        if not samples:
            return
        power = [p or 0 for p, _, _ in samples]
        hr = [h for _, h, _ in samples if h]
        cadence = [c for _, _, c in samples if c]

        old_work = msg.total_work
        work = sum(power)
        rolling = [sum(power[max(0, i - 29):i + 1]) / min(i + 1, 30) for i in range(len(power))]
        rolling = rolling[29:] or rolling
        normalized = (sum(x**4 for x in rolling) / len(rolling)) ** 0.25

        _set_if_defined(msg, "avg_power", int(work / len(power)))
        _set_if_defined(msg, "max_power", max(power))
        _set_if_defined(msg, "normalized_power", round(normalized))
        _set_if_defined(msg, "total_work", work)
        if hr:
            _set_if_defined(msg, "avg_heart_rate", round(sum(hr) / len(hr)))
            _set_if_defined(msg, "max_heart_rate", max(hr))
        if cadence:
            _set_if_defined(msg, "avg_cadence", int(sum(cadence) / len(cadence)))
            _set_if_defined(msg, "max_cadence", max(cadence))

        # Calories scale with the work done (watch's own estimate for the recorded part)
        if old_work and msg.total_calories is not None:
            _set_if_defined(msg, "total_calories", round(msg.total_calories * work / old_work))

        if threshold_power and isinstance(msg, SessionMessage):
            intensity = round(normalized) / threshold_power
            duration = msg.total_timer_time or len(power)
            _set_if_defined(msg, "intensity_factor", intensity)
            _set_if_defined(msg, "training_stress_score", duration * intensity**2 / 3600 * 100)

    @staticmethod
    def _recompute_zones(msg, samples):
        def fill(field_name, boundaries, values):
            field = msg.get_field_by_name(field_name)
            if not field or not boundaries or field.length == 0:
                return
            counts = [0] * field.length
            for v in values:
                zone = next((i for i, b in enumerate(boundaries) if v < b), len(boundaries))
                counts[min(zone, field.length - 1)] += 1
            for i, seconds in enumerate(counts):
                field.set_value(i, float(seconds))

        fill("time_in_hr_zone", msg.hr_zone_high_boundary, [h for _, h, _ in samples if h])
        fill("time_in_power_zone", msg.power_zone_high_boundary, [p or 0 for p, _, _ in samples])
