"""Checks a merged FIT file before the original activity is replaced on Garmin Connect."""

import fitdecode


def verify_output(path, expected_distance, expected_records):
    """Re-reads the output file and raises if it does not contain the merged data."""
    session = None
    record_count = 0
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == 'record':
                record_count += 1
            elif frame.name == 'session' and session is None:
                session = {f.name: f.value for f in frame.fields}

    if session is None:
        raise ValueError("Verification failed: output file has no session message.")
    if record_count != expected_records:
        raise ValueError(f"Verification failed: expected {expected_records} records, found {record_count}.")
    if abs((session.get('total_distance') or 0) - expected_distance) > 1:
        raise ValueError(f"Verification failed: session distance {session.get('total_distance')} != {expected_distance}.")

    total_seconds = session.get('total_timer_time') or 0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    time_formatted = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"

    print(f"    ✅ Verified Distance: {(session.get('total_distance') or 0) / 1000.0:.2f} km")
    print(f"    ✅ Verified Ascent: {session.get('total_ascent') or 0} m")
    print(f"    ✅ Verified Time: {time_formatted}")
    print(f"    ✅ Verified Avg Speed: {(session.get('enhanced_avg_speed') or 0) * 3.6:.2f} km/h")
    print(f"    ✅ Verified Max Speed: {(session.get('enhanced_max_speed') or 0) * 3.6:.2f} km/h")
