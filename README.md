# zwift-garmin-sync

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Support on PayPal](https://img.shields.io/badge/support-PayPal-00457C?logo=paypal&logoColor=white)](https://paypal.me/emko80/5eur)
[![Support on Revolut](https://img.shields.io/badge/support-Revolut-000000?logo=revolut&logoColor=white)](https://revolut.me/emilqzq8u?amount=500&currency=EUR)

Makes a Zwift ride count in Garmin Connect as if it had been recorded by your Garmin device.

When you ride Zwift with a Garmin watch running as an indoor ride, you end up with two recordings of the same ride:

| | Garmin watch | Zwift |
|---|---|---|
| Recognised by Garmin as a real device (training status, load, Training Effect) | ✅ | ❌ |
| Heart rate, power, cadence | ✅ | ✅ |
| Correct distance, speed, elevation, route | ❌ (estimated) | ✅ |

This tool keeps the **watch file** as the base, so Garmin still treats it as recorded by your device. It replaces the distance, speed, elevation and GPS route with the **Zwift** data, then swaps the activity on Garmin Connect for the merged version.

It also corrects for starting or stopping the watch at the wrong time. Watch data recorded before or after the Zwift ride is cut off, and seconds the watch missed (started late, stopped early) are filled in from Zwift.

---

## What the merged file contains

**From the watch (kept unchanged):**
- device identity: Garmin manufacturer, model and serial number
- heart rate, power and cadence (heart rate is repaired from Zwift if the watch lost the HR strap, see [Heart rate](#heart-rate))
- Training Effect, training load, threshold power, workout feel/RPE
- every Garmin-specific message, including the undocumented ones

**From Zwift:**
- distance, speed, elevation and GPS position, matched second by second
- session totals: distance, ascent, average and max speed
- start/end position and the route's bounding box, which Garmin Connect uses to place the activity on the map

**Laps:** from your Zwift workout, or your watch's auto-lap setting. See [Laps](#laps).

**Calculated by the tool:**
- per-lap distance, ascent, descent, average/max speed and start/end position
- for rebuilt laps: power, normalized power, work, HR, cadence, cycles, calories and time in zones
- after cutting or extending the recording: the same values plus TSS/IF for the session

The activity type is set to **Virtual Cycling** (`cycling / virtual_activity`), and the activity name gets a ✨ suffix that marks it as already merged.

---

## Laps

Indoors the watch has no GPS, so it only *estimates* distance (from power or speed). Watch auto-laps based on that estimate end at random points of the real ride: a "5 km" lap can turn out to be 2 km or 7.7 km of Zwift distance. The tool therefore rebuilds the laps:

| Your ride | Laps in the merged activity |
|---|---|
| **Zwift workout** (Zwift recorded more than one lap) | One lap per workout interval, with the same start times as in Zwift. This applies whatever the watch's auto-lap setting is. |
| **Free ride, watch auto-lap on** (for example every 5 km) | Laps re-split every 5 km of the **real Zwift distance**. The lap length is taken from the watch's own laps, so changing the auto-lap distance on the watch needs no change here. |
| **Free ride, watch auto-lap off** | No extra laps: one lap for the whole ride. |
| **Laps set with the lap button** | Kept as they are. |

**Recommended watch setting:** turn auto-lap **off** for your indoor cycling profile if you don't want laps on free rides. Workouts still get their intervals from Zwift. Turn it on when you do want distance laps; they'll be re-split correctly.

For every rebuilt lap, the following are recalculated from the per-second data:
- time, distance, ascent/descent, average/max speed, start/end position
- average/max power, normalized power, work
- average/max HR and cadence, cycles
- calories, as the lap's share of the session's work
- time in HR and power zones

Garmin-only lap values that can't be recalculated from the per-second data are copied from the watch lap that overlaps the new lap the most. Session totals (including Training Effect and training load) are never affected by how the laps are split.

## Heart rate

Sometimes the watch loses the connection to the HR strap during the ride. It doesn't always show this as missing heart rate: it can keep recording the **last value** for the rest of the ride (for example 137 bpm for an hour). Zwift, connected to the same strap on its own, usually still has the real heart rate, so the tool repairs it:

| Watch heart rate | What happens |
|---|---|
| **Frozen:** one value repeated for at least 30 s while Zwift's heart rate changes by at least 5 bpm | Replaced with Zwift's heart rate |
| **Missing** for some seconds | Filled from Zwift |
| Steady (one value for a while, Zwift shows the same) | Left alone. Real heart rate can stay on one value for about a minute. |
| Frozen, but Zwift lost the strap too | Left as recorded, with a ⚠️ warning in the output |

Average/max heart rate and time in HR zones of the ride and the affected laps are then recalculated. Power, cadence and power zones stay exactly as the watch recorded them.

**Training Effect and training load can't be repaired:** the watch calculates them during the ride from the heart rate it had, frozen value included.

## Requirements

- A Garmin Connect account and a Zwift account
- A Garmin watch or bike computer recording the ride, ideally paired to your trainer or power meter and HR strap
- Windows, macOS or Linux. Python is only needed if you run from source.

## Installation

### Option 1: download the program (no Python needed)

1. Download the file for your system from the [latest release](https://github.com/leemmoox/zwift-garmin-sync/releases/latest):

   | System | File |
   |---|---|
   | Windows | `zwift-garmin-sync-windows-x64.zip` |
   | macOS, Apple Silicon (M1 and newer) | `zwift-garmin-sync-macos-arm64.zip` |
   | macOS, Intel | `zwift-garmin-sync-macos-intel.zip` |
   | Linux | `zwift-garmin-sync-linux-x64.zip` |

2. Unzip it into a folder of your choice. The program keeps its settings (`.env`) and downloaded rides (`fit/`) **in that folder**.
3. Run it:
   - **Windows:** double-click `zwift-garmin-sync.exe`. Windows SmartScreen may warn about an unknown publisher, because the program isn't signed. Click **More info → Run anyway**.
   - **macOS:** open Terminal in the folder and run `./zwift-garmin-sync`. macOS blocks unsigned programs the first time. Either allow it in **System Settings → Privacy & Security → Open Anyway**, or run `xattr -d com.apple.quarantine zwift-garmin-sync` once.
   - **Linux:** run `./zwift-garmin-sync` in a terminal.

On the first run the program asks for your Zwift and Garmin logins and saves them in `.env` next to the program.

### Option 2: run from source

Needs Python 3.12 or newer (required by `garminconnect`; developed with 3.14).

```bash
git clone https://github.com/leemmoox/zwift-garmin-sync.git
cd zwift-garmin-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run it with `python main.py`. Logins are asked for on the first run, or you can copy `.env.example` to `.env` and fill it in.

## Configuration (`.env`)

The `.env` file is next to the program (or in the project folder when running from source). The first run creates it; edit it to change a login or a setting.

| Variable | Default | Meaning |
|---|---|---|
| `ZWIFT_USERNAME` | – | Zwift login email |
| `ZWIFT_PASSWORD` | – | Zwift password |
| `GARMIN_EMAIL` | – | Garmin Connect login email |
| `GARMIN_PASSWORD` | – | Garmin Connect password |
| `MAX_TIME_DIFF_SEC` | `10` | If the watch starts or ends within this many seconds of the Zwift ride, its recording is left as it is. Larger differences are cut or filled in. |
| `MIN_OVERLAP_PCT` | `80` | The watch recording must cover at least this percentage of the Zwift ride, otherwise the merge is refused. This stops an unrelated activity (for example a walk after the ride) from being replaced. |

Garmin login tokens are cached in `~/.garminconnect`, so you don't log in from scratch on every run and are less likely to be rate-limited.

---

## Usage

Run all commands from the repository root with the virtualenv activated.

### Automatic mode (normal use)

After the ride, once both the watch and Zwift have synced, run the program (double-click on Windows), or from source:

```bash
python main.py
```

1. Gets your latest Zwift activity and downloads its FIT file.
2. Finds the Garmin Connect activity recorded during that ride: of your last 10 activities, the one that overlaps the Zwift ride the most. Runs, outdoor rides and other activities before or after the ride are skipped. If that activity's name already contains ✨, the tool stops because the ride is already merged.
3. Downloads the original FIT file of that Garmin activity.
4. Checks both files are the same ride, aligns them, repairs heart rate if needed, merges them and verifies the result.
5. **Deletes the original activity on Garmin Connect** and uploads the merged file.
6. Renames the new activity to `<Zwift activity name> ✨`.

Downloaded and generated files are kept in `fit/` next to the program:

| File | Content |
|---|---|
| `fit/gYYYYMMDD-HHMM.fit` | Original Garmin recording (your backup if anything goes wrong) |
| `fit/zYYYYMMDD-HHMM.fit` | Zwift recording |
| `fit/fYYYYMMDD-HHMM.fit` | Merged file that was uploaded |

If a `g…` or `z…` file already exists, it's used instead of downloading again.

### Manual mode

Use this when the ride you want isn't the latest one, or when you already have both FIT files:

```bash
python main.py -g path/to/garmin.fit -z path/to/zwift.fit [-i <garmin-activity-id>] [-n "Activity name"] [-o merged.fit]
```

With the downloaded program, use `zwift-garmin-sync` (`zwift-garmin-sync.exe` on Windows) instead of `python main.py`.

**Without `-i`**, only the merged file is written and nothing changes on Garmin Connect. You can then upload it yourself (Garmin Connect → Import Data). **With `-i`**, that activity is deleted and replaced by the merged file.

| Flag | Meaning |
|---|---|
| `-g`, `--garmin` | Garmin FIT file (required) |
| `-z`, `--zwift` | Zwift FIT file (required) |
| `-i`, `--id` | ID of the Garmin Connect activity to replace: the number in the activity URL (optional, see above) |
| `-n`, `--name` | Activity name. ✨ is appended; the default is `Zwift Activity ✨` |
| `-o`, `--output` | Where to write the merged file (default `manual_fixed.fit`) |
| `--debug` | Show full error details instead of a one-line message |
| `--version` | Show the program version |

### Example output

```
1. Validating that both files record the same ride...
    Watch covers 100.0% of the Zwift ride (start +2 s, end +284 s; min required: 80%)
    ✅ Validation passed successfully!

2. Aligning watch recording to the Zwift ride: cut 284 s at the end

3. Laps: 27 laps from the Zwift workout
...
6. Verifying output file...
    ✅ Verified Distance: 25.28 km
```

---

## Limitations and things to know

- **The original activity is deleted before the upload.** Garmin rejects a re-upload of the same recording while the original exists, so it can't be the other way round. The merged file is fully built and verified before anything is deleted, and the original FIT file stays in `fit/`. If the upload fails, upload that file manually in Garmin Connect (Import Data).
- **Training Effect and training load** are calculated by the watch while you ride and are kept as they are. Garmin Connect doesn't recalculate them from an uploaded file, so seconds filled in from Zwift aren't included, seconds that were cut off still are, and a frozen heart rate (see [Heart rate](#heart-rate)) still affects them.
- **FTP auto-detection** happens on the device during the ride. Re-uploading a file doesn't trigger it.
- **Rebuilt laps** copy the Garmin-only lap fields that can't be recalculated from the per-second data (for example Garmin's proprietary lap values) from the watch lap they overlap most.
- **Map location:** Zwift worlds are placed at real coordinates (for example, Watopia is near the Solomon Islands), so that's where the map shows your ride.
- **Unofficial APIs:** both the Zwift API and the `garminconnect` library are unofficial and can break when Zwift or Garmin change something. Dependencies are pinned in `requirements.txt` for that reason.
- **Calories** for cut or filled-in time are scaled by the work done. Garmin's own calorie formula isn't public.

## Troubleshooting

| Problem | Cause / fix |
|---|---|
| `No Garmin Connect activity overlaps the Zwift ride` | The watch hasn't synced the ride to Garmin Connect yet, or it isn't among your last 10 activities. Wait for the sync, or use manual mode. |
| `Watch covers only X% of the Zwift ride` | The watch recorded less than `MIN_OVERLAP_PCT` of the Zwift ride (for example, it was started very late). Merge it in manual mode with a lower `MIN_OVERLAP_PCT`, or leave it. |
| `Activity '…' is already synced and patched!` | The latest Garmin activity already has ✨. Nothing to do. |
| `Could not find the uploaded activity; rename it manually` | Garmin processed the upload too slowly. The activity is uploaded, just not renamed. |
| Garmin authentication fails / HTTP 429 | Too many logins. Wait a while; the tokens in `~/.garminconnect` prevent this on later runs. |

---

## Support the project

If this tool saves you time, you can buy me a coffee. Thank you, it keeps the project going! ❤️

[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/emko80/5eur)
[![Revolut](https://img.shields.io/badge/Revolut-000000?style=for-the-badge&logo=revolut&logoColor=white)](https://revolut.me/emilqzq8u?amount=500&currency=EUR)

---

## Developer guide

### Project layout

```
main.py                 CLI: automatic and manual mode
zwift-garmin-sync.spec  PyInstaller build of the standalone program
scripts/release.py      Releases a new version (see "Releasing a new version")
lib/
├── app.py              Where .env and fit/ live, first-run setup, console handling
├── garmin/client.py    Garmin Connect API: login, download, delete, upload, rename
├── zwift/client.py     Zwift API: login, latest activity, FIT download
└── fit/                FIT file processing (no network access)
    ├── merger.py       ActivityMerger: the pipeline below
    ├── zwift_track.py  Reads the Zwift FIT file into per-second samples (ZwiftTrack)
    ├── align.py        Cuts/extends the watch recording to the Zwift ride window
    ├── heart_rate.py   Replaces frozen or missing watch heart rate with Zwift's
    ├── laps.py         Rebuilds laps: Zwift workout intervals, or auto-laps on Zwift distance
    ├── enrich.py       Writes Zwift data into watch records and lap/session totals
    ├── stats.py        Recalculates lap/session summaries the way the watch does
    ├── verify.py       Checks the output file before anything is deleted
    └── utils.py        Shared fit_tool helpers
tests/                  pytest suite (see "Tests" below)
```

`lib/garmin` and `lib/zwift` only talk to web services, and `lib/fit` only works on files. That means you can test any merge offline with cached files from `fit/`.

### The pipeline (`ActivityMerger.process`)

```
read Zwift FIT ──► check overlap ──► align ──► heart rate ──► laps ──► enrich ──► save ──► verify
 zwift_track.py      merger.py      align.py  heart_rate.py  laps.py  enrich.py           verify.py
```

1. **Read Zwift** (`read_zwift_fit` → `ZwiftRide`): reads the session totals, the lap start times, and one sample per second: distance, speed, altitude, position, power, HR and cadence.
2. **Check overlap**: the watch recording must cover `MIN_OVERLAP_PCT` of the Zwift ride.
3. **Align** (`Aligner`): if the start or end differs by more than `MAX_TIME_DIFF_SEC`:
   - drops watch records (and their companion messages) outside the Zwift window, and laps that fall completely outside it
   - inserts new records built from Zwift data for seconds the watch missed
   - moves the first lap/session start and last lap/session end, and renumbers laps
   - rebuilds the accumulated-power counter so it starts at 0, keeping the power meter's own increments
   - recalculates power, NP, work, HR, cadence, calories, TSS/IF and time in zones for the session and the affected laps
4. **Heart rate** (`HeartRateRepair`): finds runs of one repeated heart rate (≥ 30 s) during which Zwift's heart rate varies (≥ 5 bpm) and replaces them with Zwift's, fills seconds without heart rate from Zwift, and recalculates average/max heart rate and HR zone time of the session and affected laps. Long frozen runs without Zwift heart rate are only reported.
5. **Laps** (`LapRebuilder`): if Zwift has more than one lap, each Zwift lap becomes a lap. Otherwise, if all watch laps are distance auto-laps, they're re-split every N km of Zwift distance, where N is the watch's auto-lap distance. Each new lap is a copy of the watch lap it overlaps most, with times, stats, calories (its share of the session's work) and time in zones recalculated. Manual laps are left alone.
6. **Enrich** (`apply_zwift_data`): for every record, sets distance, speed, altitude and position from the Zwift sample at that timestamp. For every lap, sets distance, ascent, descent, average/max speed and start/end position for the lap's time window. The session gets Zwift's totals and the route's bounding box. Sport/sub-sport become `cycling/virtual_activity`.
7. **Save**: does a full re-encode (`to_file(preserve=False)`).
8. **Verify**: re-reads the output with `fitdecode` and checks the record count and session distance.

### FIT file notes (useful when changing `lib/fit`)

Two libraries are used:
- **`fitdecode`** for read-only work. It's simple and returns readable values.
- **`fit_tool`** for editing the watch file.

Things that weren't obvious:

- **A field is only written if it's in the message definition.** fit_tool encodes only the fields listed in a message's definition. Adding a field the watch never recorded (for example GPS indoors) means adding it to both the definition record and the data message's own copy of the definition. `utils.add_field_definitions` does this, and `utils.set_field` makes the field growable before setting it.
- **Full re-encode.** Once definitions change, the original bytes can't be reused, so files are saved with `preserve=False`. On an unmodified file this produces byte-identical output, so nothing is lost.
- **Garmin quirks seen on a Forerunner 965:**
  - Session, lap, activity and time-in-zone messages come *before* the records, and their `timestamp` equals the activity start, not the end. The end of a lap/session is `start_time + total_elapsed_time`.
  - Every record is followed by an undocumented message `233` with no timestamp. It's dropped or kept together with its record.
  - Message `104` is a periodic sample with its own timestamp (field `253`, FIT epoch).
  - Lap start times are whole seconds but durations are to the millisecond, so `start + duration` doesn't land exactly on the next lap's start. What the watch keeps exact is that lap durations add up to the session duration, so `align.py` works from the session start plus the previous lap durations.
- **Inserting messages:** new messages need their definition in front of them, and whatever definition was active for that local message number must be restored afterwards. `utils.insert_messages` does both.
- **Invalid values:** fit_tool returns the raw "invalid" marker (for example `255` for missing HR). Always read sensor values with `utils.valid_value`.
- **Zwift quirk:** Zwift records repeat field names (for example two `distance` fields, one of them empty). Keep the first non-empty value.
- **Units:** fit_tool timestamps are in milliseconds; FIT data is per second (`utils.secs`). Positions are written in degrees but read by fitdecode as semicircles (`SEMICIRCLES_TO_DEG`).
- **How the watch calculates summaries** (checked against 144 sessions and laps by `tests/test_watch_calibration.py`):
  - work comes from the power meter's accumulated-power counter, which is sampled faster than once a second. It's more accurate than adding up per-second power, which can be off by up to 5%.
  - average power is work ÷ number of records, rounded
  - NP is the 4th-power mean of the 30 s rolling average, and the rolling average runs across the whole activity: it does not restart at a lap
  - average cadence is truncated and excludes zeros; average HR is rounded
  - time in zones is time: a record followed by a recording gap counts for the whole gap, and a value exactly on a zone boundary counts in the zone above it (`value < boundary`)

  When a lap's per-second power adds up to clearly less than the watch's own work (sensor dropouts), the watch's NP can't be reproduced from the file. That happens on a few laps; the difference is a few watts.

### Tests

```bash
pip install -r requirements-dev.txt
pytest                              # all rides, about 3 minutes
ZG_TEST_MAX_RIDES=2 pytest          # only the 2 newest rides, about 40 seconds
pytest tests/test_zwift_track.py    # unit tests only, no ride files needed
```

Most tests use **your own rides**: the watch and Zwift files the app keeps in `fit/`. Ride files contain personal data (weight, HR, device serials), so they're never committed. Use another folder with `ZG_TEST_FIT_DIR=/path/to/rides`. Files are paired by the start time in their names (`gYYYYMMDD-HHMM.fit` / `zYYYYMMDD-HHMM.fit`). Watch files that were already merged (marked `virtual_activity`) are skipped, because they aren't real watch recordings.

| File | Checks |
|---|---|
| `test_merge.py` | For every ride: device identity and other messages kept, watch power/cadence unchanged and HR changed only where missing or frozen, distance/GPS on every record, session = Zwift distance, bounding box contains the route, laps follow each other and add up to the session, each lap has time in zones, workout intervals = Zwift laps, free-ride laps = auto-lap distance |
| `test_watch_calibration.py` | `stats.py` reproduces the watch's own session and lap values from its records |
| `test_heart_rate.py` | A heart rate frozen on purpose in a real ride is replaced with Zwift's, and ride totals and zones come out as without the freeze; good heart rate is left alone |
| `test_align.py` | A recording cut at both ends and refilled from Zwift matches the full recording; cut files stay consistent; an unrelated activity is refused; unmodified files re-encode byte-identically |
| `test_garmin_match.py` | Automatic mode picks the Garmin activity that overlaps the Zwift ride, not a run or ride before or after it |
| `test_zwift_track.py` | `ZwiftTrack` lookups on synthetic data |

For a pure refactor, also compare output files before and after the change. They should be byte-identical:

```bash
python -c "from lib.fit import ActivityMerger; ActivityMerger('fit/gX.fit', 'fit/zX.fit', '/tmp/before.fit').process()"
# ... make the change ...
python -c "from lib.fit import ActivityMerger; ActivityMerger('fit/gX.fit', 'fit/zX.fit', '/tmp/after.fit').process()"
cmp /tmp/before.fit /tmp/after.fit
```

### Releasing a new version

```bash
python scripts/release.py patch     # 1.0.0 -> 1.0.1 (fixes)
python scripts/release.py minor     # 1.0.0 -> 1.1.0 (new features)
python scripts/release.py 2.0.0     # explicit version
```

The script checks that you're on an up-to-date `main` with nothing uncommitted, runs the tests (`--quick` for the 2 newest rides only), sets the version in `lib/__init__.py`, commits, tags, asks before pushing, and then waits for the GitHub build and prints the release link or what failed.

By hand, the same is: set `__version__` in `lib/__init__.py`, commit, then `git tag -a v1.1.0 -m "zwift-garmin-sync 1.1.0" && git push origin main v1.1.0`.

GitHub Actions (`.github/workflows/release.yml`) then runs the tests, checks that the tag matches the version, builds the program on Windows, macOS (Apple Silicon and Intel) and Linux, checks that each one starts, and attaches the zip files to the release. A test build without a release can be started by hand from the **Actions** tab.

To build the program locally for your own system: `pip install pyinstaller && pyinstaller zwift-garmin-sync.spec` (output in `dist/`).

To inspect any FIT file:

```python
import fitdecode
with fitdecode.FitReader("fit/gX.fit") as fit:
    for frame in fit:
        if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == "session":
            print({f.name: f.value for f in frame.fields if f.value is not None})
```

---

## License

[MIT](LICENSE). Not affiliated with or endorsed by Zwift or Garmin.
