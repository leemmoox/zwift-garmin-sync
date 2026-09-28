import os
import sys
import argparse
from lib.zwift import Zwift
from lib.garmin import Garmin
from lib.merger import ActivityMerger

FIT_DIR = "fit"

def run_automated():
    """Automatický režim: Stiahne z API, skontroluje cache, opaví a nahradí na Garmine."""
    os.makedirs(FIT_DIR, exist_ok=True)
    
    # 1. Authenticate & fetch Garmin activity first
    garmin_client = Garmin()
    activities = garmin_client.client.get_activities(0, 1)
    
    if activities:
        latest_garmin_name = activities[0].get("activityName", "")
        # Ak už aktivita obsahuje ✨, pipeline sa preskočí
        if "✨" in latest_garmin_name:
            print(f"✨ Activity '{latest_garmin_name}' is already synced and patched!")
            sys.exit(0)

    # 2. Fetch Zwift activity & formatted name
    zwift_client = Zwift()
    zwift_act = zwift_client.get_latest_activity()
    
    raw_name = zwift_act.get("name")
    activity_name = f"{raw_name} ✨" if raw_name and not raw_name.endswith("✨") else (raw_name or "Zwift Activity ✨")
    print(f"-> Target activity name: '{activity_name}'")
    
    zwift_fit, z_time = zwift_client.get_fit_file(zwift_act, folder=FIT_DIR)
    
    # 3. Download raw FIT from Garmin
    old_activity_id, garmin_fit, g_time = garmin_client.download_latest_activity_fit(folder=FIT_DIR)
    
    # 4. Merge data
    output_file = os.path.join(FIT_DIR, f"f{g_time}.fit")
    
    merger = ActivityMerger(
        garmin_path=garmin_fit,
        zwift_path=zwift_fit,
        output_path=output_file,
        custom_name=activity_name
    )
    merger.process()

    # 5. Delete raw activity and upload patched version
    garmin_client.delete_activity(old_activity_id)
    garmin_client.upload_activity(output_file, activity_name=activity_name)


def run_manual(garmin_path, zwift_path, activity_id, custom_name, output_path):
    """Manuálny režim: Spracuje zadané lokálne FIT súbory a nahradí aktivitu na Garmine."""
    merger = ActivityMerger(
        garmin_path=garmin_path,
        zwift_path=zwift_path,
        output_path=output_path,
        custom_name=custom_name
    )
    final_name = merger.process()

    print(f"\nInitiating Garmin Connect replacement for Activity ID {activity_id}...")
    garmin_client = Garmin()
    
    garmin_client.delete_activity(activity_id)
    garmin_client.upload_activity(output_path, activity_name=final_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Zwift to Garmin FIT Sync & Merger. Runs automatically if no flags are provided."
    )
    parser.add_argument(
        "-g", "--garmin",
        help="Path to local Garmin FIT file (Manual mode)"
    )
    parser.add_argument(
        "-z", "--zwift",
        help="Path to local Zwift FIT file (Manual mode)"
    )
    parser.add_argument(
        "-i", "--id",
        help="Garmin Activity ID to delete and replace (Manual mode)"
    )
    parser.add_argument(
        "-n", "--name",
        default=None,
        help="Custom title for the activity"
    )
    parser.add_argument(
        "-o", "--output",
        default=f"manual_fixed.fit",
        help=f"Output path for merged FIT file (default: manual_fixed.fit)"
    )

    args = parser.parse_args()

    # Ak sú zadané manuálne argumenty, overíme ich prítomnosť
    if args.garmin or args.zwift or args.id:
        if not (args.garmin and args.zwift and args.id):
            print("❌ Error: Manual mode requires all three flags: -g (Garmin FIT), -z (Zwift FIT), and -i (Garmin Activity ID).")
            sys.exit(1)
            
        run_manual(
            garmin_path=args.garmin,
            zwift_path=args.zwift,
            activity_id=args.id,
            custom_name=args.name,
            output_path=args.output
        )
    else:
        # Bez argumentov spustíme plnú automatiku
        run_automated()