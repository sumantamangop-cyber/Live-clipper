"""Speech captions for a clip: the transcript's words in short groups, the spoken word highlighted.

Written as an ASS file (one event per word), so the active word can change colour as it is said.
Kept here rather than in the main app's subtitles.py so the live clipper stays self-contained.
"""
from pathlib import Path

MAX_WORDS = 4      # 2-4 words on screen at once: short groups read as "rapid" captions
MAX_CHARS = 16     # a group also stops before it is wider than one line at FONT_SIZE
MAX_GAP_S = 0.8    # a pause this long starts a new group
FONT = "Impact"
FONT_SIZE = 128    # pixels on the 1080x1920 frame (PlayRes below is that size): big, like the reference
OUTLINE = 5
SHADOW = 2
MARGIN_V = 420     # bottom captions: above the app's buttons, in the lower band of a 16:9 stream
TOP_MARGIN_V = 840  # top captions (gaming look): just under the divider below the facecam
ACTIVE = "{\\c&H00E5FF&}"  # the spoken word: yellow (BGR order)
RESET = "{\\c}"            # back to the style's white


def _header(alignment: int, margin_v: int) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H66000000,0,0,0,0,100,100,0,0,1,{OUTLINE},{SHADOW},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))  # centiseconds
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _clean(word: str) -> str:
    """Uppercase, and kept from starting an ASS override block."""
    return word.upper().replace("\\", "/").replace("{", "(").replace("}", ")")


def groups(words, t0: float, t1: float) -> list:
    """Lists of words inside [t0, t1), split into short groups at pauses, MAX_WORDS and MAX_CHARS."""
    inside = [w for w in words if t0 <= w["start"] < t1 and w["w"]]
    out, cur = [], []
    for w in inside:
        width = sum(len(x["w"]) for x in cur) + len(cur) + len(w["w"])  # letters plus spaces
        if cur and (len(cur) == MAX_WORDS or width > MAX_CHARS or w["start"] - cur[-1]["end"] > MAX_GAP_S):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def write_ass(words, t0: float, t1: float, path, top: bool = False) -> bool:
    """Writes the ASS file for [t0, t1), times relative to t0. False when nothing is said there.

    ``top`` puts the captions just under the facecam divider (gaming look) instead of the bottom.
    """
    events = []
    for g in groups(words, t0, t1):
        for i, cur in enumerate(g):
            start = max(0.0, cur["start"] - t0)
            # each word's event runs until the next word starts, so the highlight never lags
            nxt = g[i + 1]["start"] if i + 1 < len(g) else cur["end"]
            end = max(start + 0.2, nxt - t0)
            text = " ".join(f"{ACTIVE}{_clean(w['w'])}{RESET}" if j == i else _clean(w["w"])
                            for j, w in enumerate(g))
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    if not events:
        return False
    alignment, margin = (8, TOP_MARGIN_V) if top else (2, MARGIN_V)
    Path(path).write_text(_header(alignment, margin) + "\n".join(events) + "\n", encoding="utf-8")
    return True
