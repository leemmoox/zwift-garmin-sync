"""Writes Zwift distance, speed, altitude and position into Garmin records and lap/session totals."""

from fit_tool.definition_message import DefinitionMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Sport, SubSport

from lib.fit.utils import SEMICIRCLES_TO_DEG, add_field_definitions, secs, set_field

# Fields written into Garmin messages. Missing ones (e.g. GPS/altitude, which the watch
# does not record indoors) are added to the message definitions before writing.
# heart_rate: may have been repaired from Zwift (lib/fit/heart_rate.py)
RECORD_FIELDS = ["distance", "enhanced_speed", "enhanced_altitude", "position_lat", "position_long", "heart_rate"]
SUMMARY_FIELDS = [
    "sport", "sub_sport", "total_distance", "total_ascent", "total_descent",
    "avg_speed", "max_speed", "enhanced_avg_speed", "enhanced_max_speed",
    "start_position_lat", "start_position_long", "end_position_lat", "end_position_long",
]
# Bounding box of the route, used by Garmin Connect to place the activity on the map
BOUNDING_BOX_FIELDS = ["nec_lat", "nec_long", "swc_lat", "swc_long"]
FIELDS_BY_MESSAGE = {
    RecordMessage: RECORD_FIELDS,
    LapMessage: SUMMARY_FIELDS,
    SessionMessage: SUMMARY_FIELDS + BOUNDING_BOX_FIELDS,
    SportMessage: ["sport", "sub_sport"],
}
MESSAGES_BY_GLOBAL_ID = {cls.ID: cls for cls in FIELDS_BY_MESSAGE}


def apply_zwift_data(garmin_fit, ride):
    """Updates garmin_fit in place with data from a ZwiftRide. Returns (updated record count, updated lap count)."""
    track = ride.track

    records = laps = 0
    for record in garmin_fit.records:
        msg = record.message
        # Every message we write must carry the fields in its definition (both the
        # definition record and the data message's own copy of it).
        if isinstance(msg, DefinitionMessage):
            cls = MESSAGES_BY_GLOBAL_ID.get(msg.global_id)
            if cls:
                add_field_definitions(msg, cls, FIELDS_BY_MESSAGE[cls])
            continue
        if type(msg) not in FIELDS_BY_MESSAGE:
            continue

        add_field_definitions(msg.definition_message, type(msg), FIELDS_BY_MESSAGE[type(msg)])
        msg.set_definition_message(msg.definition_message)

        if isinstance(msg, RecordMessage):
            _apply_to_record(msg, track)
            records += 1
            continue

        set_field(msg, 'sport', Sport.CYCLING.value)
        set_field(msg, 'sub_sport', SubSport.VIRTUAL_ACTIVITY.value)
        if isinstance(msg, SportMessage):
            continue

        _apply_to_summary(msg, ride)
        if isinstance(msg, LapMessage):
            laps += 1

    return records, laps


def _apply_to_record(msg, track):
    t = secs(msg.timestamp)
    s = track.at(t)
    set_field(msg, 'distance', s['distance'])
    set_field(msg, 'enhanced_speed', s['speed'] if track.start <= t <= track.end else 0.0)
    if s['altitude'] is not None:
        set_field(msg, 'enhanced_altitude', s['altitude'])
    if s['lat'] is not None and s['long'] is not None:
        set_field(msg, 'position_lat', s['lat'] * SEMICIRCLES_TO_DEG)
        set_field(msg, 'position_long', s['long'] * SEMICIRCLES_TO_DEG)


def _apply_to_summary(msg, ride):
    """Lap/session totals for the message's time window; the session uses Zwift's own totals."""
    track = ride.track
    # Garmin writes the lap/session timestamp as the start, so derive the end from elapsed time
    t_start = secs(msg.start_time)
    t_end = t_start + round(msg.total_elapsed_time or 0)
    timer_time = msg.total_timer_time or msg.total_elapsed_time or 0

    ascent, descent = track.climb_between(t_start, t_end)
    if isinstance(msg, SessionMessage):
        distance = ride.distance
        if ride.session.get('total_ascent') is not None:
            ascent = ride.session['total_ascent']
    else:
        distance = track.distance_between(t_start, t_end)

    avg_speed = distance / timer_time if timer_time else 0.0
    max_speed = track.max_speed_between(t_start, t_end)

    set_field(msg, 'total_distance', distance)
    set_field(msg, 'total_ascent', round(ascent))
    set_field(msg, 'total_descent', round(descent))
    for name, value in [('avg_speed', avg_speed), ('enhanced_avg_speed', avg_speed),
                        ('max_speed', max_speed), ('enhanced_max_speed', max_speed)]:
        set_field(msg, name, value)

    _apply_positions(msg, track.window(t_start, t_end))


def _apply_positions(msg, samples):
    """Start/end position, and for the session the bounding box, of the route in the window."""
    points = [(s['lat'], s['long']) for s in samples if s['lat'] is not None and s['long'] is not None]
    if not points:
        return
    (start_lat, start_long), (end_lat, end_long) = points[0], points[-1]
    values = {
        'start_position_lat': start_lat, 'start_position_long': start_long,
        'end_position_lat': end_lat, 'end_position_long': end_long,
    }
    if isinstance(msg, SessionMessage):
        lats, longs = [p[0] for p in points], [p[1] for p in points]
        values.update(nec_lat=max(lats), nec_long=max(longs), swc_lat=min(lats), swc_long=min(longs))
    for name, semicircles in values.items():
        set_field(msg, name, semicircles * SEMICIRCLES_TO_DEG)
