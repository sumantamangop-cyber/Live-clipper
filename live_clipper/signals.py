"""Turn chat activity and loudness into moments worth clipping. Pure functions, no I/O.

Every signal becomes an event ``(stream_seconds, weight)``. ``candidates`` turns
events into clip windows that start ``pre`` seconds before the event (the
build-up) and end ``post`` seconds after it.
"""
import re
import statistics

_RMS = re.compile(r"pts_time:([\d.]+).*?RMS_level=(-?[\d.]+|-inf)", re.S)


def parse_levels(text: str) -> list:
    """(time, dB) pairs from ffmpeg's ametadata print output (one line per second)."""
    out = []
    for t, db in _RMS.findall(text):
        if db != "-inf":
            out.append((float(t), float(db)))
    return out


def audio_events(levels, rise_db: float = 10.0) -> list:
    """Seconds whose loudness sits ``rise_db`` above the clip's median level."""
    if not levels:
        return []
    median = statistics.median(db for _, db in levels)
    return [(t, (db - median) / rise_db) for t, db in levels if db >= median + rise_db]


def chat_events(stamps, bucket: float = 10.0, factor: float = 3.0, min_count: int = 10) -> list:
    """A bucket of chat counts as an event when it is busy both in absolute and relative terms."""
    if not stamps:
        return []
    counts = {}
    for s in stamps:
        counts[int(s // bucket)] = counts.get(int(s // bucket), 0) + 1
    last = max(counts)
    per_bucket = [counts.get(i, 0) for i in range(last + 1)]
    baseline = statistics.median(per_bucket)
    threshold = max(min_count, factor * baseline)
    return [(i * bucket + bucket / 2, c / min_count) for i, c in counts.items() if c >= threshold]


def is_hot(stamps, now: float, window: float = 60.0, min_msgs: int = 30) -> bool:
    """True when at least ``min_msgs`` chat messages arrived in the last ``window`` seconds."""
    return sum(1 for s in stamps if now - window <= s <= now) >= min_msgs


def candidates(events, pre: float = 40.0, post: float = 15.0, limit: int = 3, max_len: float = 60.0) -> list:
    """Merge overlapping windows around events; the heaviest ``limit`` windows win.

    A merged window is capped at ``max_len`` seconds so a busy stretch cannot become one long clip.
    """
    windows = sorted((max(0.0, t - pre), t + post, w) for t, w in events)
    merged = []
    for start, end, weight in windows:
        if merged and start <= merged[-1][1]:
            s, e, w = merged[-1]
            merged[-1] = (s, max(e, end), w + weight)
        else:
            merged.append((start, end, weight))
    merged.sort(key=lambda x: -x[2])
    return [{"start": round(s, 2), "end": round(min(e, s + max_len), 2), "score": round(w, 2)}
            for s, e, w in merged[:limit]]
