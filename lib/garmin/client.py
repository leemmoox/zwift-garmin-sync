import io
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from garminconnect import Garmin as GarminClient


class Garmin:
    def __init__(self, email=None, password=None):
        self.email = email or os.getenv("GARMIN_EMAIL")
        self.password = password or os.getenv("GARMIN_PASSWORD")

        if not self.email or not self.password:
            raise ValueError(
                "Garmin credentials (GARMIN_EMAIL, GARMIN_PASSWORD) are missing in environment variables."
            )

        self.client = None
        self._authenticate()

    def _authenticate(self):
        """Authenticates with Garmin Connect using token caching to avoid 429 rate limits."""
        print("Authenticating with Garmin Connect...")
        tokenstore = str(Path("~/.garminconnect").expanduser())

        try:
            self.client = GarminClient(self.email, self.password)
            self.client.login(tokenstore)
            print("✅ Successfully authenticated with Garmin Connect!")
        except Exception as e:
            raise Exception(f"Garmin authentication failed: {e}") from e

    def find_activity_for_ride(self, ride_start, ride_end, count=10):
        """Of the latest `count` activities, the one overlapping the ride (UTC seconds) the most.

        Returns (activity, overlapping seconds), or (None, 0) when none overlaps. Runs,
        outdoor rides and other activities around the Zwift ride don't overlap it.
        """
        print(f"Looking for the matching ride among the last {count} Garmin Connect activities...")
        best, best_overlap = None, 0
        for activity in self.client.get_activities(0, count) or []:
            start_gmt = activity.get("startTimeGMT")
            if not start_gmt:
                continue
            start = datetime.strptime(start_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            duration = activity.get("elapsedDuration") or activity.get("duration") or 0
            overlap = min(start + duration, ride_end) - max(start, ride_start)
            if overlap > best_overlap:
                best, best_overlap = activity, overlap
        return best, best_overlap

    def download_activity_fit(self, activity, folder="fit") -> tuple[str, str, str]:
        """
        Downloads the activity's original FIT file, or loads it from fit/gYYYYMMDD-HHMM.fit if it exists.
        Returns tuple: (activity_id, file_path, formatted_timestamp)
        """
        os.makedirs(folder, exist_ok=True)

        activity_id = str(activity.get("activityId"))
        activity_name = activity.get("activityName", "Unknown Activity")

        start_str = activity.get("startTimeLocal", "")
        dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        timestamp = dt.strftime("%Y%m%d-%H%M")

        file_path = os.path.join(folder, f"g{timestamp}.fit")

        activity_type = (activity.get("activityType") or {}).get("typeKey", "unknown type")
        print(f"-> Garmin activity: '{activity_name}' ({activity_type}, ID: {activity_id})")

        # Kontrola cache ihneď pred výpisom o sťahovaní
        if os.path.exists(file_path):
            print(f"📦 Found cached Garmin FIT file: {file_path}")
            return activity_id, file_path, timestamp

        print(f"Downloading raw data for activity ID {activity_id} ({timestamp})...")
        data = self.client.download_activity(
            activity_id, dl_fmt=self.client.ActivityDownloadFormat.ORIGINAL
        )

        if data[:2] == b"PK":
            print("Extracted data is a ZIP archive. Unpacking FIT file...")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                fit_filenames = [
                    name for name in z.namelist() if name.lower().endswith(".fit")
                ]
                if not fit_filenames:
                    raise Exception(
                        "No .fit file found inside the downloaded Garmin ZIP archive."
                    )
                fit_data = z.read(fit_filenames[0])
            print("✅ FIT file successfully unpacked from ZIP archive!")
        else:
            fit_data = data

        with open(file_path, "wb") as f:
            f.write(fit_data)

        print(f"✅ Garmin FIT file successfully saved as '{file_path}'!")
        return activity_id, file_path, timestamp

    def upload_activity(
        self, file_path: str, activity_name: str = None, start_time: datetime = None
    ):
        """Uploads a fixed FIT file to Garmin Connect and updates its name via API.

        start_time (UTC) identifies the new activity if the upload response does not contain its ID.
        """
        print(f"Uploading fixed FIT file '{file_path}' to Garmin Connect...")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File to upload not found: {file_path}")

        try:
            upload_response = self.client.upload_activity(file_path)
        except Exception as e:
            raise Exception(f"Failed to upload activity to Garmin Connect: {e}") from e
        print("✅ Activity successfully uploaded to Garmin Connect!")

        if activity_name:
            new_activity_id = self._find_uploaded_activity_id(upload_response, start_time)
            if new_activity_id:
                self.client.set_activity_name(new_activity_id, activity_name)
                print(
                    f"✨ Activity {new_activity_id} successfully renamed to '{activity_name}'!"
                )
            else:
                print(
                    f"⚠️ Could not find the uploaded activity; rename it manually to '{activity_name}'."
                )

        return upload_response

    def _find_uploaded_activity_id(self, upload_response, start_time, timeout_sec=60):
        """Gets the new activity ID from the upload response, or waits for an activity with the same start time."""
        result = (upload_response or {}).get("detailedImportResult", {})
        successes = result.get("successes") or []
        if successes and successes[0].get("internalId"):
            return successes[0]["internalId"]

        if start_time is None:
            return None

        # Garmin processes uploads asynchronously; never fall back to "latest activity"
        expected = start_time.strftime("%Y-%m-%d %H:%M:%S")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(3)
            for activity in self.client.get_activities(0, 5):
                if activity.get("startTimeGMT") == expected:
                    return activity.get("activityId")
        return None

    def delete_activity(self, activity_id: str):
        """Deletes an old/raw activity from Garmin Connect by its ID."""
        print(f"Deleting old raw activity (ID: {activity_id}) from Garmin Connect...")
        try:
            self.client.delete_activity(activity_id)
            print(
                f"✅ Activity ID {activity_id} successfully deleted from Garmin Connect!"
            )
        except Exception as e:
            raise Exception(f"Failed to delete activity {activity_id}: {e}") from e
