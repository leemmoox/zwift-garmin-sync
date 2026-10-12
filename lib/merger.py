import os
from bisect import bisect_right
from datetime import datetime

import fitdecode
from fit_tool.definition_message import DefinitionMessage
from fit_tool.field_definition import FieldDefinition
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Sport, SubSport

SEMICIRCLES_TO_DEG = 180.0 / 2**31

# Fields written into Garmin messages. Missing ones (e.g. GPS/altitude, which the watch
# does not record indoors) are added to the message definitions before writing.
RECORD_FIELDS = ["distance", "enhanced_speed", "enhanced_altitude", "position_lat", "position_long"]
SUMMARY_FIELDS = [
    "sport", "sub_sport", "total_distance", "total_ascent", "total_descent",
    "avg_speed", "max_speed", "enhanced_avg_speed", "enhanced_max_speed",
]
FIELDS_BY_MESSAGE = {
    RecordMessage: RECORD_FIELDS,
    LapMessage: SUMMARY_FIELDS,
    SessionMessage: SUMMARY_FIELDS,
    SportMessage: ["sport", "sub_sport"],
}


def _set(msg, name, value):
    field = msg.get_field_by_name(name)
    field.growable = True
    field.set_value(0, value)


class ZwiftTrack:
    """Per-second Zwift samples (distance, speed, altitude, position) indexed by timestamp."""

    def __init__(self, samples):
        if not samples:
            raise ValueError("Zwift FIT file contains no record data.")
        self.samples = sorted(samples, key=lambda s: s["t"])
        self.times = [s["t"] for s in self.samples]
        self.start = self.times[0]
        self.end = self.times[-1]

    def at(self, t):
        """Sample at time t (last sample at or before t; clamped at both ends)."""
        i = max(bisect_right(self.times, t) - 1, 0)
        return self.samples[i]

    def window(self, t_start, t_end):
        lo = bisect_right(self.times, t_start - 1)
        hi = bisect_right(self.times, t_end)
        return self.samples[lo:hi]

    def distance_between(self, t_start, t_end):
        start_distance = self.at(t_start)["distance"] if t_start > self.start else 0.0
        return max(self.at(t_end)["distance"] - start_distance, 0.0)

    def climb_between(self, t_start, t_end):
        ascent = descent = 0.0
        alts = [s["altitude"] for s in self.window(t_start, t_end) if s["altitude"] is not None]
        for a, b in zip(alts, alts[1:]):
            if b > a:
                ascent += b - a
            else:
                descent += a - b
        return ascent, descent

    def max_speed_between(self, t_start, t_end):
        return max((s["speed"] for s in self.window(t_start, t_end)), default=0.0)


class ActivityMerger:
    def __init__(self, garmin_path, zwift_path, output_path="garmin_fixed.fit"):
        self.garmin_path = garmin_path
        self.zwift_path = zwift_path
        self.output_path = output_path
        self.start_time = None  # Garmin activity start (UTC), set by process()

        # Load max allowed start time difference from environment (default: 10.0 s)
        self.max_time_diff = float(os.getenv("MAX_TIME_DIFF_SEC", "10"))

    def _get_start_time(self, file_path):
        """Extracts the start_time from the session message of a FIT file."""
        with fitdecode.FitReader(file_path) as fit:
            for frame in fit:
                if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == 'session':
                    for f in frame.fields:
                        if f.name == 'start_time' and f.value:
                            return f.value
        return None

    def _read_zwift(self):
        """Reads Zwift session totals and per-second record samples."""
        session = {}
        samples = []
        with fitdecode.FitReader(self.zwift_path) as fit:
            for frame in fit:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                if frame.name == 'session' and not session:
                    session = {f.name: f.value for f in frame.fields}
                elif frame.name == 'record':
                    # Zwift repeats some field names (e.g. 'distance') with empty values; keep the real one
                    v = {}
                    for f in frame.fields:
                        if f.value is not None:
                            v.setdefault(f.name, f.value)
                    if v.get('timestamp') is None or v.get('distance') is None:
                        continue
                    speed = v.get('enhanced_speed')
                    if speed is None:
                        speed = v.get('speed')
                    altitude = v.get('enhanced_altitude')
                    if altitude is None:
                        altitude = v.get('altitude')
                    samples.append({
                        't': int(v['timestamp'].timestamp()),
                        'distance': float(v['distance']),
                        'speed': float(speed or 0.0),
                        'altitude': altitude,
                        'lat': v.get('position_lat'),
                        'long': v.get('position_long'),
                    })
        return session, ZwiftTrack(samples)

    def process(self):
        """Replaces distance, speed, altitude and position in the Garmin file with Zwift data (matched by timestamp) and recomputes lap/session totals."""
        if not os.path.exists(self.garmin_path) or not os.path.exists(self.zwift_path):
            raise FileNotFoundError("Error: One or both input FIT files do not exist.")

        print(f"\n-> Garmin file: {self.garmin_path}")
        print(f"-> Zwift file:  {self.zwift_path}")

        print("\n0. Validating activity start times...")
        garmin_start = self._get_start_time(self.garmin_path)
        zwift_start = self._get_start_time(self.zwift_path)

        # Data is matched by timestamp, so refuse to merge files we cannot align
        if not (isinstance(garmin_start, datetime) and isinstance(zwift_start, datetime)):
            raise ValueError("Validation Error: Could not extract start times from both FIT files.")

        time_diff = abs((garmin_start - zwift_start).total_seconds())
        print(f"    Time difference: {time_diff:.1f} seconds (Max allowed: {self.max_time_diff:.1f}s)")
        if time_diff > self.max_time_diff:
            raise ValueError(
                f"Validation Error: Garmin and Zwift files do not match! "
                f"Time difference is {time_diff:.1f}s (max allowed is {self.max_time_diff:.1f}s)."
            )
        print("    ✅ Time validation passed successfully!")
        self.start_time = garmin_start

        print("\n1. Reading Zwift session and per-second data...")
        zwift_session, track = self._read_zwift()
        zwift_distance = zwift_session.get('total_distance') or track.samples[-1]['distance']
        print(f"    ✅ Zwift: {zwift_distance / 1000.0:.2f} km, {len(track.samples)} samples")

        print("\n2. Merging Zwift data into Garmin records...")
        garmin_fit = FitFile.from_file(self.garmin_path)

        # Every message we write must carry the fields in its definition (both the
        # definition record and the data message's own copy of it).
        field_defs = {
            cls: [(f.field_id, f.base_type) for f in (cls().get_field_by_name(n) for n in names)]
            for cls, names in FIELDS_BY_MESSAGE.items()
        }
        global_ids = {cls.ID: cls for cls in FIELDS_BY_MESSAGE}

        def ensure_fields(definition, cls):
            for field_id, base_type in field_defs[cls]:
                if definition.get_field_definition(field_id) is None:
                    definition.add_field_definition(FieldDefinition(field_id, base_type.size, base_type))

        records = laps = 0
        for record in garmin_fit.records:
            msg = record.message
            if isinstance(msg, DefinitionMessage):
                if msg.global_id in global_ids:
                    ensure_fields(msg, global_ids[msg.global_id])
                continue
            if type(msg) not in FIELDS_BY_MESSAGE:
                continue

            ensure_fields(msg.definition_message, type(msg))
            msg.set_definition_message(msg.definition_message)

            if isinstance(msg, RecordMessage):
                t = msg.timestamp // 1000
                s = track.at(t)
                _set(msg, 'distance', s['distance'])
                _set(msg, 'enhanced_speed', s['speed'] if track.start <= t <= track.end else 0.0)
                if s['altitude'] is not None:
                    _set(msg, 'enhanced_altitude', s['altitude'])
                if s['lat'] is not None and s['long'] is not None:
                    _set(msg, 'position_lat', s['lat'] * SEMICIRCLES_TO_DEG)
                    _set(msg, 'position_long', s['long'] * SEMICIRCLES_TO_DEG)
                records += 1
                continue

            _set(msg, 'sport', Sport.CYCLING.value)
            _set(msg, 'sub_sport', SubSport.VIRTUAL_ACTIVITY.value)
            if isinstance(msg, SportMessage):
                continue

            # Garmin writes the lap/session timestamp as the start, so derive the end from elapsed time
            t_start = msg.start_time // 1000
            t_end = t_start + round(msg.total_elapsed_time or 0)
            timer_time = msg.total_timer_time or msg.total_elapsed_time or 0

            ascent, descent = track.climb_between(t_start, t_end)
            if isinstance(msg, SessionMessage):
                distance = zwift_distance
                if zwift_session.get('total_ascent') is not None:
                    ascent = zwift_session['total_ascent']
            else:
                distance = track.distance_between(t_start, t_end)
                laps += 1

            avg_speed = distance / timer_time if timer_time else 0.0
            max_speed = track.max_speed_between(t_start, t_end)

            _set(msg, 'total_distance', distance)
            _set(msg, 'total_ascent', round(ascent))
            _set(msg, 'total_descent', round(descent))
            for name, value in [('avg_speed', avg_speed), ('enhanced_avg_speed', avg_speed),
                                ('max_speed', max_speed), ('enhanced_max_speed', max_speed)]:
                _set(msg, name, value)

        print(f"    ✅ Updated {records} records and {laps} laps")

        print(f"\n3. Saving merged file to '{self.output_path}'...")
        # Full re-encode: definitions changed, so original bytes cannot be reused
        garmin_fit.to_file(self.output_path, preserve=False)
        print("    ✅ Merged FIT file created successfully!")

        print("\n4. Verifying output file...")
        self._verify(zwift_distance, records)
        print("\n✅ Done! The file is verified and ready to be uploaded to Garmin Connect.")

    def _verify(self, expected_distance, expected_records):
        """Re-reads the output file and raises if it does not contain the merged data."""
        session = None
        record_count = 0
        with fitdecode.FitReader(self.output_path) as fit:
            for frame in fit:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                if frame.name == 'record':
                    record_count += 1
                elif frame.name == 'session' and session is None:
                    session = {f.name: f.value for f in frame.fields}

        if session is None:
            raise ValueError("Verification failed: output file has no session message.")
        if record_count != expected_records:
            raise ValueError(f"Verification failed: expected {expected_records} records, found {record_count}.")
        if abs((session.get('total_distance') or 0) - expected_distance) > 1:
            raise ValueError(f"Verification failed: session distance {session.get('total_distance')} != {expected_distance}.")

        total_seconds = session.get('total_timer_time') or 0
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        time_formatted = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"

        print(f"    ✅ Verified Distance: {(session.get('total_distance') or 0) / 1000.0:.2f} km")
        print(f"    ✅ Verified Ascent: {session.get('total_ascent') or 0} m")
        print(f"    ✅ Verified Time: {time_formatted}")
        print(f"    ✅ Verified Avg Speed: {(session.get('enhanced_avg_speed') or 0) * 3.6:.2f} km/h")
        print(f"    ✅ Verified Max Speed: {(session.get('enhanced_max_speed') or 0) * 3.6:.2f} km/h")
