import argparse
import os
import sys
import traceback

from lib import __version__
from lib.app import FIT_DIR, configure_console, ensure_credentials, load_settings, pause_if_double_clicked
from lib.fit import ActivityMerger
from lib.garmin import Garmin
from lib.zwift import Zwift

SYNC_TAG = "✨"


def tag_name(name):
    """Appends the ✨ tag that marks an activity as already merged."""
    name = name or "Zwift Activity"
    return name if name.endswith(SYNC_TAG) else f"{name} {SYNC_TAG}"


def run_automated():
    """Automatický režim: Stiahne z API, skontroluje cache, opaví a nahradí na Garmine."""
    os.makedirs(FIT_DIR, exist_ok=True)
    ensure_credentials("garmin", "zwift")

    # 1. Authenticate & fetch Garmin activity first
    garmin_client = Garmin()
    activities = garmin_client.client.get_activities(0, 1)

    if activities:
        latest_garmin_name = activities[0].get("activityName", "")
        # Ak už aktivita obsahuje ✨, pipeline sa preskočí
        if SYNC_TAG in latest_garmin_name:
            print(f"✨ Activity '{latest_garmin_name}' is already synced and patched!")
            return

    # 2. Fetch Zwift activity & formatted name
    zwift_client = Zwift()
    zwift_act = zwift_client.get_latest_activity()

    activity_name = tag_name(zwift_act.get("name"))
    print(f"-> Target activity name: '{activity_name}'")

    zwift_fit, z_time = zwift_client.get_fit_file(zwift_act, folder=FIT_DIR)

    # 3. Download raw FIT from Garmin
    old_activity_id, garmin_fit, g_time = garmin_client.download_latest_activity_fit(
        folder=FIT_DIR
    )

    # 4. Merge data
    output_file = os.path.join(FIT_DIR, f"f{g_time}.fit")

    merger = ActivityMerger(
        garmin_path=garmin_fit,
        zwift_path=zwift_fit,
        output_path=output_file,
    )
    merger.process()

    # 5. Delete raw activity and upload patched version
    garmin_client.delete_activity(old_activity_id)
    garmin_client.upload_activity(
        output_file, activity_name=activity_name, start_time=merger.start_time
    )


def run_manual(garmin_path, zwift_path, activity_id, custom_name, output_path):
    """Manuálny režim: Spracuje zadané lokálne FIT súbory; s ID aktivity ju aj nahradí na Garmine."""
    merger = ActivityMerger(
        garmin_path=garmin_path,
        zwift_path=zwift_path,
        output_path=output_path,
    )
    merger.process()
    activity_name = tag_name(custom_name)

    if not activity_id:
        print(f"\nMerged file saved as '{output_path}'. Nothing was changed on Garmin Connect.")
        print("Upload it yourself (Garmin Connect → Import Data), or pass -i <activity ID> to replace an activity.")
        return
    ensure_credentials("garmin")

    print(f"\nInitiating Garmin Connect replacement for Activity ID {activity_id}...")
    garmin_client = Garmin()

    garmin_client.delete_activity(activity_id)
    garmin_client.upload_activity(
        output_path, activity_name=activity_name, start_time=merger.start_time
    )


def parse_args():
    parser = argparse.ArgumentParser(
        prog="zwift-garmin-sync",
        description="Merges your latest Zwift ride into your Garmin watch recording and replaces it on "
                    "Garmin Connect. Runs automatically if no files are given.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-g", "--garmin", help="Path to local Garmin FIT file (manual mode)")
    parser.add_argument("-z", "--zwift", help="Path to local Zwift FIT file (manual mode)")
    parser.add_argument(
        "-i", "--id",
        help="Garmin activity ID to delete and replace (manual mode). Without it, only the merged file is written.",
    )
    parser.add_argument("-n", "--name", default=None, help="Custom title for the activity")
    parser.add_argument(
        "-o", "--output", default="manual_fixed.fit",
        help="Output path for merged FIT file (default: manual_fixed.fit)",
    )
    parser.add_argument("--debug", action="store_true", help="Show full error details")
    return parser.parse_args()


def main():
    args = parse_args()
    # Load .env before anything reads settings (e.g. MAX_TIME_DIFF_SEC in the merger)
    load_settings()

    if args.garmin or args.zwift or args.id:
        if not (args.garmin and args.zwift):
            raise ValueError("Manual mode needs both -g (Garmin FIT) and -z (Zwift FIT).")
        run_manual(
            garmin_path=args.garmin,
            zwift_path=args.zwift,
            activity_id=args.id,
            custom_name=args.name,
            output_path=args.output,
        )
    else:
        # Bez argumentov spustíme plnú automatiku
        run_automated()


if __name__ == "__main__":
    configure_console()
    exit_code = 0
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        exit_code = 130
    except Exception as e:
        if "--debug" in sys.argv:
            traceback.print_exc()
        print(f"\n❌ Error: {e}")
        exit_code = 1
    pause_if_double_clicked()
    sys.exit(exit_code)
