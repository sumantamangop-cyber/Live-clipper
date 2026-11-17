"""Speech captions for a clip: the transcript's words, grouped into short lines, as an SRT file."""
from pathlib import Path

MAX_WORDS = 4      # words per caption line
MAX_GAP_S = 0.8    # a pause this long starts a new line


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def lines(words, t0: float, t1: float) -> list:
    """(start, end, text) for the words inside [t0, t1), times relative to t0."""
    inside = [w for w in words if t0 <= w["start"] < t1 and w["w"]]
    out, cur = [], []
    for w in inside:
        if cur and (len(cur) == MAX_WORDS or w["start"] - cur[-1]["end"] > MAX_GAP_S):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [(max(0.0, g[0]["start"] - t0), max(0.0, g[-1]["end"] - t0), " ".join(x["w"] for x in g))
            for g in out]


def write_srt(words, t0: float, t1: float, path) -> bool:
    """Writes the SRT for [t0, t1). False when there is nothing to say in that window."""
    cues = lines(words, t0, t1)
    if not cues:
        return False
    body = "".join(f"{i}\n{srt_time(a)} --> {srt_time(max(b, a + 0.5))}\n{text}\n\n"
                   for i, (a, b, text) in enumerate(cues, 1))
    Path(path).write_text(body, encoding="utf-8")
    return True
