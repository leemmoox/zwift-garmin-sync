# Zwift → Garmin FIT merger

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
- heart rate, power and cadence
- Training Effect, training load, threshold power, workout feel/RPE
- every Garmin-specific message, including the undocumented ones

**From Zwift:**
- distance, speed, elevation and GPS position, matched second by second
- session totals: distance, ascent, average and max speed

**Calculated by the tool:**
- per-lap distance, ascent, descent, average and max speed
- after cutting or extending the recording: power, normalized power, work, HR, cadence, time in zones, calories and TSS/IF

The activity type is set to **Virtual Cycling** (`cycling / virtual_activity`), and the activity name gets a ✨ suffix that marks it as already merged.

---

## Requirements

- Python 3.12 or newer (required by `garminconnect`; developed with 3.14)
- A Garmin Connect account and a Zwift account
- A Garmin watch or bike computer recording the ride, ideally paired to your trainer or power meter and HR strap

## Installation

```bash
git clone <repo-url> zg
cd zg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`.

## Configuration (`.env`)

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

After the ride, once both the watch and Zwift have synced:

```bash
python main.py
```

1. Gets your latest Garmin Connect activity. If its name already contains ✨, the tool stops because that ride is already merged.
2. Gets your latest Zwift activity and downloads its FIT file.
3. Downloads the original FIT file of the latest Garmin activity.
4. Checks both files are the same ride, aligns them, merges them and verifies the result.
5. **Deletes the original activity on Garmin Connect** and uploads the merged file.
6. Renames the new activity to `<Zwift activity name> ✨`.

Downloaded and generated files are kept in `fit/`:

| File | Content |
|---|---|
| `fit/gYYYYMMDD-HHMM.fit` | Original Garmin recording (your backup if anything goes wrong) |
| `fit/zYYYYMMDD-HHMM.fit` | Zwift recording |
| `fit/fYYYYMMDD-HHMM.fit` | Merged file that was uploaded |

If a `g…` or `z…` file already exists, it's used instead of downloading again.

### Manual mode

Use this when the ride you want isn't the latest one, or when you already have both FIT files:

```bash
python main.py -g path/to/garmin.fit -z path/to/zwift.fit -i <garmin-activity-id> [-n "Activity name"] [-o merged.fit]
```

| Flag | Meaning |
|---|---|
| `-g`, `--garmin` | Garmin FIT file (required) |
| `-z`, `--zwift` | Zwift FIT file (required) |
| `-i`, `--id` | ID of the Garmin Connect activity to replace: the number in the activity URL (required) |
| `-n`, `--name` | Activity name. ✨ is appended; the default is `Zwift Activity ✨` |
| `-o`, `--output` | Where to write the merged file (default `manual_fixed.fit`) |

### Example output

```
1. Validating that both files record the same ride...
    Watch covers 100.0% of the Zwift ride (start +2 s, end +284 s; min required: 80%)
    ✅ Validation passed successfully!

2. Aligning watch recording to the Zwift ride: cut 284 s at the end
...
5. Verifying output file...
    ✅ Verified Distance: 25.28 km
```

---

## Limitations and things to know

- **The original activity is deleted before the upload.** Garmin rejects a re-upload of the same recording while the original exists, so it can't be the other way round. The merged file is fully built and verified before anything is deleted, and the original FIT file stays in `fit/`. If the upload fails, upload that file manually in Garmin Connect (Import Data).
- **Training Effect and training load** are calculated by the watch while you ride and are kept as they are. Garmin Connect doesn't recalculate them from an uploaded file, so seconds filled in from Zwift aren't included, and seconds that were cut off still are.
- **FTP auto-detection** happens on the device during the ride. Re-uploading a file doesn't trigger it.
- **Map location:** Zwift worlds are placed at real coordinates (for example, Watopia is near the Solomon Islands), so that's where the map shows your ride.
- **Unofficial APIs:** both the Zwift API and the `garminconnect` library are unofficial and can break when Zwift or Garmin change something. Dependencies are pinned in `requirements.txt` for that reason.
- **Calories** for cut or filled-in time are scaled by the work done. Garmin's own calorie formula isn't public.

## Troubleshooting

| Problem | Cause / fix |
|---|---|
| `Watch covers only X% of the Zwift ride` | The latest Garmin and Zwift activities aren't the same ride (for example, the watch hasn't synced yet). Wait for the sync, or use manual mode. |
| `Activity '…' is already synced and patched!` | The latest Garmin activity already has ✨. Nothing to do. |
| `Could not find the uploaded activity; rename it manually` | Garmin processed the upload too slowly. The activity is uploaded, just not renamed. |
| Garmin authentication fails / HTTP 429 | Too many logins. Wait a while; the tokens in `~/.garminconnect` prevent this on later runs. |

---

## Developer guide

### Project layout

```
main.py                 CLI: automatic and manual mode
lib/
├── garmin/client.py    Garmin Connect API: login, download, delete, upload, rename
├── zwift/client.py     Zwift API: login, latest activity, FIT download
└── fit/                FIT file processing (no network access)
    ├── merger.py       ActivityMerger: the pipeline below
    ├── zwift_track.py  Reads the Zwift FIT file into per-second samples (ZwiftTrack)
    ├── align.py        Cuts/extends the watch recording to the Zwift ride window
    ├── enrich.py       Writes Zwift data into watch records and lap/session totals
    ├── verify.py       Checks the output file before anything is deleted
    └── utils.py        Shared fit_tool helpers
```

`lib/garmin` and `lib/zwift` only talk to web services, and `lib/fit` only works on files. That means you can test any merge offline with cached files from `fit/`.

### The pipeline (`ActivityMerger.process`)

```
read Zwift FIT ──► check overlap ──► align ──► enrich ──► save ──► verify
 zwift_track.py      merger.py      align.py  enrich.py           verify.py
```

1. **Read Zwift** (`read_zwift_fit`): reads the session totals plus one sample per second: distance, speed, altitude, position, power, HR and cadence.
2. **Check overlap**: the watch recording must cover `MIN_OVERLAP_PCT` of the Zwift ride.
3. **Align** (`Aligner`): if the start or end differs by more than `MAX_TIME_DIFF_SEC`:
   - drops watch records (and their companion messages) outside the Zwift window, and laps that fall completely outside it
   - inserts new records built from Zwift data for seconds the watch missed
   - moves the first lap/session start and last lap/session end, and renumbers laps
   - recalculates power, NP, work, HR, cadence, calories, TSS/IF and time in zones for the session and the affected laps
4. **Enrich** (`apply_zwift_data`): for every record, sets distance, speed, altitude and position from the Zwift sample at that timestamp. For every lap, sets distance, ascent, descent and average/max speed for the lap's time window. The session gets Zwift's totals. Sport/sub-sport become `cycling/virtual_activity`.
5. **Save**: does a full re-encode (`to_file(preserve=False)`).
6. **Verify**: re-reads the output with `fitdecode` and checks the record count and session distance.

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
- **Invalid values:** fit_tool returns the raw "invalid" marker (for example `255` for missing HR). Always read sensor values with `utils.valid_value`.
- **Zwift quirk:** Zwift records repeat field names (for example two `distance` fields, one of them empty). Keep the first non-empty value.
- **Units:** fit_tool timestamps are in milliseconds; FIT data is per second (`utils.secs`). Positions are written in degrees but read by fitdecode as semicircles (`SEMICIRCLES_TO_DEG`).
- **How the watch rounds:**
  - average power is truncated and includes zeros
  - average cadence is truncated and excludes zeros
  - average HR and NP are rounded
  - NP is the 4th-power mean of the 30 s rolling average
  - an HR or power value exactly on a zone boundary counts in the zone above it (`value < boundary`)

  With these rules the recalculated values match what the watch writes.

### Testing a change

There's no automated test suite yet. Because the merger is fully offline, the most useful check is to compare output before and after your change on real rides:

```bash
# before the change
python -c "from lib.fit import ActivityMerger; ActivityMerger('fit/gX.fit', 'fit/zX.fit', '/tmp/before.fit').process()"
# after the change
python -c "from lib.fit import ActivityMerger; ActivityMerger('fit/gX.fit', 'fit/zX.fit', '/tmp/after.fit').process()"
cmp /tmp/before.fit /tmp/after.fit   # refactors should produce identical bytes
```

To test alignment, cut a real recording with `Aligner` against a shortened `ZwiftTrack` (simulating a watch started late or stopped early), merge it with the full Zwift file, and compare the session values with the original watch file. They should match within about 1%.

To inspect any FIT file:

```python
import fitdecode
with fitdecode.FitReader("fit/gX.fit") as fit:
    for frame in fit:
        if frame.frame_type == fitdecode.FIT_FRAME_DATA and frame.name == "session":
            print({f.name: f.value for f in frame.fields if f.value is not None})
```
