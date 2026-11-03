"""Reads a FIT file into plain Python values for assertions."""

from collections import Counter, defaultdict
from functools import lru_cache

import fitdecode


@lru_cache(maxsize=None)
def read_fit(path):
    """Returns {'counts': Counter of message names, '<name>': [field dicts]}.

    Repeated field names keep the first non-empty value (Zwift writes duplicates).
    Cached per path for the test run, so callers must not modify the result.
    """
    data = defaultdict(list)
    counts = Counter()
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            counts[frame.name] += 1
            values = {}
            for f in frame.fields:
                if f.value is not None:
                    values.setdefault(f.name, f.value)
            data[frame.name].append(values)
    data["counts"] = counts
    return data
