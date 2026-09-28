import os
import io
import time
import zipfile
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from garminconnect import Garmin as GarminClient

class Garmin:
    def __init__(self, email=None, password=None):
        load_dotenv()
        self.email = email or os.getenv("GARMIN_EMAIL")
        self.password = password or os.getenv("GARMIN_PASSWORD")
        
        if not self.email or not self.password:
            raise ValueError("Garmin credentials (GARMIN_EMAIL, GARMIN_PASSWORD) are missing in environment variables.")
            
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
            raise Exception(f"Garmin authentication failed: {e}")

    def download_latest_activity_fit(self, folder="fit") -> tuple[str, str, str]:
        """
        Downloads latest Garmin activity FIT file or loads from fit/gYYYYMMDD-HHMM.fit if exists.
        Returns tuple: (activity_id, file_path, formatted_timestamp)
        """
        os.makedirs(folder, exist_ok=True)
        
        print("Fetching latest activities from Garmin Connect...")
        activities = self.client.get_activities(0, 1)
        
        if not activities:
            raise Exception("No activities found on Garmin Connect.")
            
        latest = activities[0]
        activity_id = str(latest.get("activityId"))
        activity_name = latest.get("activityName", "Unknown Activity")
        
        start_str = latest.get("startTimeLocal", "")
        dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        timestamp = dt.strftime("%Y%m%d-%H%M")

        file_path = os.path.join(folder, f"g{timestamp}.fit")

        print(f"-> Latest Garmin activity found: '{activity_name}' (ID: {activity_id})")

        # Kontrola cache ihneď pred výpisom o sťahovaní
        if os.path.exists(file_path):
            print(f"📦 Found cached Garmin FIT file: {file_path}")
            return activity_id, file_path, timestamp

        print(f"Downloading raw data for activity ID {activity_id} ({timestamp})...")
        data = self.client.download_activity(activity_id, dl_fmt=self.client.ActivityDownloadFormat.ORIGINAL)
        
        if data[:2] == b'PK':
            print("Extracted data is a ZIP archive. Unpacking FIT file...")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                fit_filenames = [name for name in z.namelist() if name.lower().endswith('.fit')]
                if not fit_filenames:
                    raise Exception("No .fit file found inside the downloaded Garmin ZIP archive.")
                fit_data = z.read(fit_filenames[0])
            print("✅ FIT file successfully unpacked from ZIP archive!")
        else:
            fit_data = data

        with open(file_path, "wb") as f:
            f.write(fit_data)
            
        print(f"✅ Garmin FIT file successfully saved as '{file_path}'!")
        return activity_id, file_path, timestamp

    def upload_activity(self, file_path: str, activity_name: str = None):
        """Uploads a fixed FIT file to Garmin Connect and updates its name via API."""
        print(f"Uploading fixed FIT file '{file_path}' to Garmin Connect...")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File to upload not found: {file_path}")
            
        try:
            upload_response = self.client.upload_activity(file_path)
            print("✅ Activity successfully uploaded to Garmin Connect!")
            
            if activity_name:
                time.sleep(3) # Pauza pre indexáciu na serveri
                activities = self.client.get_activities(0, 1)
                if activities:
                    new_activity_id = activities[0].get("activityId")
                    self.client.set_activity_name(new_activity_id, activity_name)
                    print(f"✨ Activity {new_activity_id} successfully renamed to '{activity_name}'!")
                        
            return upload_response
        except Exception as e:
            raise Exception(f"Failed to upload activity to Garmin Connect: {e}")

    def delete_activity(self, activity_id: str):
        """Deletes an old/raw activity from Garmin Connect by its ID."""
        print(f"Deleting old raw activity (ID: {activity_id}) from Garmin Connect...")
        try:
            self.client.delete_activity(activity_id)
            print(f"✅ Activity ID {activity_id} successfully deleted from Garmin Connect!")
        except Exception as e:
            raise Exception(f"Failed to delete activity {activity_id}: {e}")