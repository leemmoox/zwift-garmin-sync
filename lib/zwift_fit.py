"""Reads a Zwift FIT file into per-second samples."""

from bisect import bisect_right

import fitdecode


class ZwiftTrack:
    """Per-second Zwift samples (distance, speed, altitude, position, power, HR, cadence) indexed by timestamp."""

    def __init__(self, samples):
        if not samples:
            raise ValueError("Zwift FIT file contains no record data.")
        self.samples = sorted(samples, key=lambda s: s["t"])
        self.times = [s["t"] for s in self.samples]
        self.start = self.times[0]
        self.end = self.times[-1]

    def at(self, t):
        """Sample at time t (last sample at or before t; clamped at both ends)."""
        i = max(bisect_right(self.times, t) - 1, 0)
        return self.samples[i]

    def window(self, t_start, t_end):
        lo = bisect_right(self.times, t_start - 1)
        hi = bisect_right(self.times, t_end)
        return self.samples[lo:hi]

    def distance_between(self, t_start, t_end):
        start_distance = self.at(t_start)["distance"] if t_start > self.start else 0.0
        return max(self.at(t_end)["distance"] - start_distance, 0.0)

    def climb_between(self, t_start, t_end):
        ascent = descent = 0.0
        alts = [s["altitude"] for s in self.window(t_start, t_end) if s["altitude"] is not None]
        for a, b in zip(alts, alts[1:]):
            if b > a:
                ascent += b - a
            else:
                descent += a - b
        return ascent, descent

    def max_speed_between(self, t_start, t_end):
        return max((s["speed"] for s in self.window(t_start, t_end)), default=0.0)


def read_zwift_fit(path):
    """Returns (session field dict, ZwiftTrack) for a Zwift FIT file."""
    session = {}
    samples = []
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == 'session' and not session:
                session = {f.name: f.value for f in frame.fields}
            elif frame.name == 'record':
                # Zwift repeats some field names (e.g. 'distance') with empty values; keep the real one
                v = {}
                for f in frame.fields:
                    if f.value is not None:
                        v.setdefault(f.name, f.value)
                if v.get('timestamp') is None or v.get('distance') is None:
                    continue
                speed = v.get('enhanced_speed')
                if speed is None:
                    speed = v.get('speed')
                altitude = v.get('enhanced_altitude')
                if altitude is None:
                    altitude = v.get('altitude')
                samples.append({
                    't': int(v['timestamp'].timestamp()),
                    'distance': float(v['distance']),
                    'speed': float(speed or 0.0),
                    'altitude': altitude,
                    'lat': v.get('position_lat'),
                    'long': v.get('position_long'),
                    'power': v.get('power'),
                    'heart_rate': v.get('heart_rate'),
                    'cadence': v.get('cadence'),
                })
    return session, ZwiftTrack(samples)
