import os
from datetime import datetime, timezone

from fit_tool.fit_file import FitFile

from lib.fit.align import Aligner, ride_window
from lib.fit.enrich import apply_zwift_data
from lib.fit.heart_rate import HeartRateRepair
from lib.fit.laps import LapRebuilder
from lib.fit.verify import verify_output
from lib.fit.zwift_track import read_zwift_fit


class ActivityMerger:
    """Pipeline: read Zwift data -> check both files are the same ride -> align the watch
    recording to the Zwift ride -> repair heart rate -> rebuild laps -> write Zwift data into
    the watch file -> save -> verify."""

    def __init__(self, garmin_path, zwift_path, output_path="garmin_fixed.fit"):
        self.garmin_path = garmin_path
        self.zwift_path = zwift_path
        self.output_path = output_path
        self.start_time = None  # Garmin activity start (UTC), set by process()

        # Start/end differences up to this many seconds are left as recorded by the watch
        self.max_time_diff = int(float(os.getenv("MAX_TIME_DIFF_SEC", "10")))
        # Minimum share of the Zwift ride the watch recording must cover
        self.min_overlap_pct = float(os.getenv("MIN_OVERLAP_PCT", "80"))

    def process(self):
        """Merges the Zwift ride into the Garmin FIT file and writes it to output_path."""
        if not os.path.exists(self.garmin_path) or not os.path.exists(self.zwift_path):
            raise FileNotFoundError("Error: One or both input FIT files do not exist.")

        print(f"\n-> Garmin file: {self.garmin_path}")
        print(f"-> Zwift file:  {self.zwift_path}")

        print("\n0. Reading Zwift session and per-second data...")
        ride = read_zwift_fit(self.zwift_path)
        print(
            f"    ✅ Zwift: {ride.distance / 1000.0:.2f} km, {len(ride.track.samples)} samples, "
            f"{len(ride.lap_starts)} laps"
        )

        print("\n1. Validating that both files record the same ride...")
        garmin_fit = FitFile.from_file(self.garmin_path)
        self._validate_overlap(garmin_fit, ride.track)
        print("    ✅ Validation passed successfully!")

        aligner = Aligner(garmin_fit, ride.track, self.max_time_diff)
        print(f"\n2. Aligning watch recording to the Zwift ride: {aligner.describe()}")
        aligner.run()
        self.start_time = datetime.fromtimestamp(aligner.new_start, tz=timezone.utc)

        heart_rate = HeartRateRepair(garmin_fit, ride.track)
        heart_rate.run()
        print(f"\n3. Heart rate: {heart_rate.describe()}")

        rebuilder = LapRebuilder(garmin_fit, ride)
        print(f"\n4. Laps: {rebuilder.describe()}")
        rebuilder.run()

        print("\n5. Merging Zwift data into Garmin records...")
        records, laps = apply_zwift_data(garmin_fit, ride)
        print(f"    ✅ Updated {records} records and {laps} laps")

        print(f"\n6. Saving merged file to '{self.output_path}'...")
        # Full re-encode: definitions changed, so original bytes cannot be reused
        garmin_fit.to_file(self.output_path, preserve=False)
        print("    ✅ Merged FIT file created successfully!")

        print("\n7. Verifying output file...")
        verify_output(self.output_path, ride.distance, records)
        print("\n✅ Done! The file is verified and ready to be uploaded to Garmin Connect.")

    def _validate_overlap(self, garmin_fit, track):
        """Raises unless the watch recording covers at least min_overlap_pct of the Zwift ride."""
        g_start, g_end = ride_window(garmin_fit)
        overlap = max(min(g_end, track.end) - max(g_start, track.start), 0)
        overlap_pct = 100.0 * overlap / max(track.end - track.start, 1)
        print(
            f"    Watch covers {overlap_pct:.1f}% of the Zwift ride "
            f"(start {g_start - track.start:+d} s, end {g_end - track.end:+d} s; min required: {self.min_overlap_pct:.0f}%)"
        )
        if overlap_pct < self.min_overlap_pct:
            raise ValueError(
                f"Validation Error: Garmin and Zwift files do not match! "
                f"Watch covers only {overlap_pct:.1f}% of the Zwift ride (min {self.min_overlap_pct:.0f}%)."
            )
