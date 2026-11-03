import os
from datetime import datetime

import requests


class Zwift:
    def __init__(self, username=None, password=None):
        self.username = username or os.getenv("ZWIFT_USERNAME")
        self.password = password or os.getenv("ZWIFT_PASSWORD")

        if not self.username or not self.password:
            raise ValueError(
                "Zwift credentials (ZWIFT_USERNAME, ZWIFT_PASSWORD) are missing in environment variables."
            )

        self.access_token = None
        self.numeric_profile_id = None
        self.session = requests.Session()
        self.base_api_url = "https://us-or-rly101.zwift.com/api"
        self._authenticate()

    def _authenticate(self):
        """Authenticates with Zwift API via OAuth token endpoint."""
        print("Authenticating with Zwift via OAuth token endpoint...")
        auth_url = (
            "https://secure.zwift.com/auth/realms/zwift/protocol/openid-connect/token"
        )

        payload = {
            "client_id": "zwift-public",
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Zwift/1.0.0",
        }

        response = self.session.post(auth_url, data=payload, headers=headers)

        if response.status_code != 200:
            raise Exception(
                f"Zwift authentication failed ({response.status_code}): {response.text}"
            )

        data = response.json()
        self.access_token = data.get("access_token")
        print("✅ Successfully authenticated with Zwift!")

        self._load_numeric_profile_id()

    def _get_headers(self) -> dict:
        """Returns standard authentication headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": "ZwiftApp/1.0.0 (Macintosh; Intel Mac OS X 10_15_7)",
        }

    def _load_numeric_profile_id(self):
        """Retrieves user profile object from us-or-rly101 gateway to parse numeric ID."""
        profile_url = f"{self.base_api_url}/profiles/me"

        response = self.session.get(profile_url, headers=self._get_headers())
        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch profile info ({response.status_code}): {response.text}"
            )

        profile_data = response.json()
        self.numeric_profile_id = profile_data.get("id")
        print(f"✅ Numeric Profile ID loaded: {self.numeric_profile_id}")

    def get_latest_activity(self) -> dict:
        """Fetches the latest activity using validated numeric profile ID."""
        if not self.numeric_profile_id:
            raise Exception("Numeric profile ID is missing.")

        feed_url = (
            f"{self.base_api_url}/profiles/{self.numeric_profile_id}/activities?limit=1"
        )

        response = self.session.get(feed_url, headers=self._get_headers())

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch Zwift activities ({response.status_code}): {response.text[:200]}"
            )

        activities = response.json()
        if not activities:
            raise Exception("No activities found on Zwift.")

        print("✅ Activity successfully fetched from Zwift!")
        return activities[0]

    def get_fit_file(self, activity_data: dict, folder="fit") -> tuple[str, str]:
        """
        Checks cache or downloads FIT file directly from S3.
        Saves as fit/zYYYYMMDD-HHMM.fit
        Returns tuple: (file_path, formatted_timestamp)
        """
        os.makedirs(folder, exist_ok=True)

        activity_id = activity_data.get("id") or activity_data.get("id_str")
        start_str = activity_data.get("startDate", "")

        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone()
        timestamp = dt.strftime("%Y%m%d-%H%M")

        file_path = os.path.join(folder, f"z{timestamp}.fit")

        # Kontrola cache ihneď pred výpisom o sťahovaní
        if os.path.exists(file_path):
            print(f"📦 Found cached Zwift FIT file: {file_path}")
            return file_path, timestamp

        bucket = activity_data.get("fitFileBucket")
        key = activity_data.get("fitFileKey")

        if not bucket or not key:
            raise Exception(
                f"Missing fitFileBucket or fitFileKey in activity {activity_id}"
            )

        s3_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        print(f"Downloading FIT file directly from S3: {s3_url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

        response = requests.get(s3_url, headers=headers, timeout=30)

        if response.status_code != 200:
            alt_s3_url = f"https://s3.us-west-2.amazonaws.com/{bucket}/{key}"
            response = requests.get(alt_s3_url, headers=headers, timeout=30)

        if response.status_code == 200 and len(response.content) > 100:
            with open(file_path, "wb") as f:
                f.write(response.content)
            print(f"✅ Zwift FIT file successfully saved as '{file_path}'!")
            return file_path, timestamp

        raise Exception(
            f"Failed to download FIT file from S3 (HTTP {response.status_code}): {response.text[:150]}"
        )
