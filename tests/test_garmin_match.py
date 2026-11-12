"""Picking the Garmin Connect activity that belongs to the Zwift ride (no network needed)."""

from datetime import datetime, timezone

from lib.garmin.client import Garmin

RIDE_START = datetime(2026, 11, 12, 16, 14, 21, tzinfo=timezone.utc).timestamp()
RIDE_END = RIDE_START + 2 * 3600


def activity(activity_id, start_gmt, minutes, type_key, name="Activity"):
    return {"activityId": activity_id, "activityName": name, "startTimeGMT": start_gmt,
            "elapsedDuration": minutes * 60.0, "activityType": {"typeKey": type_key}}


class FakeConnect:
    def __init__(self, activities):
        self.activities = activities

    def get_activities(self, start, limit):
        return self.activities[start:start + limit]


def garmin_with(activities):
    garmin = Garmin.__new__(Garmin)  # skips logging in
    garmin.client = FakeConnect(activities)
    return garmin


def test_picks_the_ride_not_a_later_run():
    activities = [  # Garmin lists the newest first
        activity(3, "2026-11-12 18:40:00", 45, "running", "Evening run"),
        activity(2, "2026-11-12 16:14:25", 120, "indoor_cycling", "Indoor cycling"),
        activity(1, "2026-11-12 07:00:00", 30, "running", "Morning run"),
    ]
    found, overlap = garmin_with(activities).find_activity_for_ride(RIDE_START, RIDE_END)
    assert found["activityId"] == 2
    assert overlap > 7000


def test_no_overlapping_activity():
    activities = [activity(1, "2026-11-11 16:00:00", 120, "indoor_cycling")]
    assert garmin_with(activities).find_activity_for_ride(RIDE_START, RIDE_END) == (None, 0)


def test_picks_the_largest_overlap():
    activities = [
        activity(2, "2026-11-12 18:10:00", 30, "walking"),  # starts just before the ride ends
        activity(1, "2026-11-12 16:20:00", 110, "indoor_cycling"),  # watch started 6 min late
    ]
    found, _ = garmin_with(activities).find_activity_for_ride(RIDE_START, RIDE_END)
    assert found["activityId"] == 1


def test_activity_without_start_time_is_skipped():
    activities = [{"activityId": 1}, activity(2, "2026-11-12 16:14:21", 120, "indoor_cycling")]
    found, _ = garmin_with(activities).find_activity_for_ride(RIDE_START, RIDE_END)
    assert found["activityId"] == 2
