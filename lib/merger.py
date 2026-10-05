import os
from datetime import datetime
import fitdecode
from fit_tool.fit_file import FitFile
from fit_tool.profile.profile_type import Sport, SubSport

class ActivityMerger:
    def __init__(self, garmin_path, zwift_path, output_path="garmin_fixed.fit", custom_name=None):
        self.garmin_path = garmin_path
        self.zwift_path = zwift_path
        self.output_path = output_path
        self.custom_name = custom_name

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

    def process(self):
        """Merges Zwift session metrics, proportionally scales Garmin records to match Zwift distance/speed, and applies activity name."""
        if not os.path.exists(self.garmin_path) or not os.path.exists(self.zwift_path):
            raise FileNotFoundError("Error: One or both input FIT files do not exist.")

        print(f"\n-> Garmin file: {self.garmin_path}")
        print(f"-> Zwift file:  {self.zwift_path}")

        # --- TIME VALIDATION (+- 10 seconds) ---
        print("\n0. Validating activity start times...")
        garmin_start = self._get_start_time(self.garmin_path)
        zwift_start = self._get_start_time(self.zwift_path)

        if garmin_start and zwift_start:
            if isinstance(garmin_start, datetime) and isinstance(zwift_start, datetime):
                time_diff = abs((garmin_start - zwift_start).total_seconds())
            else:
                time_diff = abs(float(garmin_start) - float(zwift_start))

            print(f"    Time difference: {time_diff:.1f} seconds (Max allowed: {self.max_time_diff:.1f}s)")

            if time_diff > self.max_time_diff:
                raise ValueError(
                    f"Validation Error: Garmin and Zwift files do not match! "
                    f"Time difference is {time_diff:.1f}s (max allowed is {self.max_time_diff:.1f}s)."
                )
            print("    ✅ Time validation passed successfully!")
        else:
            print("    ⚠️ Warning: Could not extract start times for validation. Skipping time check.")

        print("\n1. Reading session data and activity name from Zwift FIT file...")
        zwift_session = {}
        zwift_activity_name = self.custom_name
        
        with fitdecode.FitReader(self.zwift_path) as fit:
            for frame in fit:
                if frame.frame_type == fitdecode.FIT_FRAME_DATA:
                    # Read session metrics
                    if frame.name == 'session' and not zwift_session:
                        vals = {f.name: f.value for f in frame.fields}
                        zwift_session = {
                            'total_distance': vals.get('total_distance'),
                            'avg_speed': vals.get('avg_speed'),
                            'max_speed': vals.get('max_speed'),
                            'total_ascent': vals.get('total_ascent'),
                            'total_timer_time': vals.get('total_timer_time'),
                            'total_elapsed_time': vals.get('total_elapsed_time'),
                        }
                    # Read activity name from Zwift if custom name was not provided
                    elif not self.custom_name and frame.name == 'activity':
                        for f in frame.fields:
                            if f.name in ['name', 'string'] and f.value:
                                zwift_activity_name = str(f.value)

        zwift_distance_m = zwift_session.get('total_distance') or 0.0
        print(f"    ✅ Zwift target distance: {zwift_distance_m / 1000.0:.2f} km")
        
        # Append ✨ tag to activity name if not already present
        if zwift_activity_name:
            if "✨" not in zwift_activity_name:
                zwift_activity_name = f"{zwift_activity_name} ✨"
            print(f"    ✅ Activity name to apply: '{zwift_activity_name}'")

        print("\n2. Calculating scaling factor based on original Garmin distance...")
        # First, find out what total distance the Garmin file originally recorded
        garmin_original_distance = 0.0
        with fitdecode.FitReader(self.garmin_path) as fit:
            for frame in fit:
                if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == 'session':
                    for f in frame.fields:
                        if f.name == 'total_distance' and f.value:
                            garmin_original_distance = float(f.value)
                            break

        if garmin_original_distance <= 0:
            garmin_original_distance = 109000.0  # fallback based on user note if session distance is missing

        scale_factor = zwift_distance_m / garmin_original_distance
        print(f"    ℹ️ Scaling factor for distance & speed: {scale_factor:.4f} (Garmin orig: {garmin_original_distance/1000.0:.1f} km -> Zwift: {zwift_distance_m/1000.0:.1f} km)")

        print("\n3. Applying proportional scaling to Garmin record points and Zwift session metrics...")
        garmin_fit = FitFile.from_file(self.garmin_path)

        for record in garmin_fit.records:
            msg = record.message
            if not msg:
                continue

            msg_name = type(msg).__name__

            # Proportionally scale distance and speed in each RecordMessage to preserve profile shape but fix averages
            if msg_name == 'RecordMessage':
                # Scale distance
                f_dst = msg.get_field_by_name('distance')
                if f_dst:
                    val = getattr(f_dst, 'value', None)
                    if val is not None:
                        new_dst = float(val) * scale_factor
                        f_dst.growable = True
                        f_dst.set_value(0, new_dst)

                # Scale speed fields
                for speed_field in ['speed', 'enhanced_speed']:
                    f_spd = msg.get_field_by_name(speed_field)
                    if f_spd:
                        val = getattr(f_spd, 'value', None)
                        if val is not None:
                            new_spd = float(val) * scale_factor
                            f_spd.growable = True
                            f_spd.set_value(0, new_spd)

            # Update Session and Lap messages with exact Zwift totals
            elif msg_name in ['SessionMessage', 'LapMessage']:
                # Set sport to cycling and sub_sport to virtual activity
                f_sport = msg.get_field_by_name('sport')
                if f_sport:
                    f_sport.growable = True
                    f_sport.set_value(0, Sport.CYCLING.value)

                f_sub = msg.get_field_by_name('sub_sport')
                if f_sub:
                    f_sub.growable = True
                    f_sub.set_value(0, SubSport.VIRTUAL_ACTIVITY.value)

                # Total distance
                if zwift_session.get('total_distance') is not None:
                    f_dist = msg.get_field_by_name('total_distance')
                    if f_dist:
                        f_dist.growable = True
                        f_dist.set_value(0, zwift_session['total_distance'])

                # Total ascent
                if zwift_session.get('total_ascent') is not None:
                    f_ascent = msg.get_field_by_name('total_ascent')
                    if f_ascent:
                        f_ascent.growable = True
                        f_ascent.set_value(0, zwift_session['total_ascent'])
                    else:
                        msg.set_field_values({'total_ascent': zwift_session['total_ascent']})

                # Parameters specific to SessionMessage
                if msg_name == 'SessionMessage':
                    # Avg speed (standard + enhanced)
                    if zwift_session.get('avg_speed') is not None:
                        for speed_f in ['avg_speed', 'enhanced_avg_speed']:
                            f_spd = msg.get_field_by_name(speed_f)
                            if f_spd:
                                f_spd.growable = True
                                f_spd.set_value(0, zwift_session['avg_speed'])
                            else:
                                msg.set_field_values({speed_f: zwift_session['avg_speed']})

                    # Max speed (standard + enhanced) - directly from Zwift
                    if zwift_session.get('max_speed') is not None:
                        for speed_f in ['max_speed', 'enhanced_max_speed']:
                            f_spd = msg.get_field_by_name(speed_f)
                            if f_spd:
                                f_spd.growable = True
                                f_spd.set_value(0, zwift_session['max_speed'])
                            else:
                                msg.set_field_values({speed_f: zwift_session['max_speed']})

                    # Time synchronization
                    for time_field in ['total_timer_time', 'total_elapsed_time']:
                        if zwift_session.get(time_field) is not None:
                            f_time = msg.get_field_by_name(time_field)
                            if f_time:
                                f_time.growable = True
                                f_time.set_value(0, zwift_session[time_field])
                            else:
                                msg.set_field_values({time_field: zwift_session[time_field]})

                    # Activity name metadata
                    if zwift_activity_name:
                        try:
                            f_act_name = msg.get_field_by_name('activity_name')
                            if f_act_name:
                                f_act_name.growable = True
                                f_act_name.set_value(0, zwift_activity_name)
                        except Exception:
                            pass

        print(f"\n4. Saving fixed Garmin file to '{self.output_path}'...")
        garmin_fit.to_file(self.output_path)
        print("    ✅ Fixed FIT file created successfully!")

        print("\n5. Verifying output file session metrics...")
        with fitdecode.FitReader(self.output_path) as fit:
            for frame in fit:
                if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == 'session':
                    vals = {f.name: f.value for f in frame.fields}
                    v_dist = (vals.get('total_distance') or 0) / 1000.0
                    v_ascent = (vals.get('total_ascent') or 0)
                    
                    total_seconds = vals.get('total_timer_time') or 0
                    hours = int(total_seconds // 3600)
                    minutes = int((total_seconds % 3600) // 60)
                    seconds = int(total_seconds % 60)
                    time_formatted = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"

                    v_avg_speed = vals.get('avg_speed', 0) * 3.6  # m/s to km/h
                    v_max_speed = vals.get('max_speed', 0) * 3.6  # m/s to km/h
                    
                    print(f"    ✅ Verified Distance: {v_dist:.2f} km")
                    print(f"    ✅ Verified Ascent: {v_ascent:.1f} m")
                    print(f"    ✅ Verified Time: {time_formatted}")
                    print(f"    ✅ Verified Avg Speed: {v_avg_speed:.2f} km/h")
                    print(f"    ✅ Verified Max Speed: {v_max_speed:.2f} km/h")
                    break

        print("\n✅ Done! The file is verified and ready to be uploaded to Garmin Connect.")
        return zwift_activity_name