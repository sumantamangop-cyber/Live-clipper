"""Cut a window from a recording into a 9:16 clip (1080x1920), in one of three looks.

- ``full``: the whole 16:9 frame, centred over a blurred copy of itself. Nothing is cut off.
- ``zoom``: the centre of the frame, cropped to fill the screen. For close-ups.
- ``gaming``: the facecam on top (about 40% of the height), the game below, a thin divider
  between them. Needs the facecam's box in the source frame (``cam``).
Captions come from the ASS file, which carries its own styling.
"""
import os
import subprocess
from pathlib import Path

from . import config

CAM_H = 768            # facecam block height on the 1920-tall frame (40%)
DIVIDER_PX = 8         # thin line between the facecam and the game
DIVIDER_COLOUR = "0xFFE500"


def _video_graph(subs, look: str = "full", cam=None) -> str:
    if look == "zoom":
        graph = "[0:v]crop=trunc(ih*9/32)*2:ih,scale=1080:1920,setsar=1"
    elif look == "gaming":
        if not cam:
            raise ValueError("the gaming look needs the facecam box")
        x, y, w, h = cam
        graph = ("[0:v]split=2[c][g];"
                 f"[c]crop={w}:{h}:{x}:{y},scale=1080:{CAM_H}:force_original_aspect_ratio=increase,"
                 f"crop=1080:{CAM_H},pad=1080:{CAM_H + DIVIDER_PX}:0:0:color={DIVIDER_COLOUR}[top];"
                 f"[g]scale=1080:{1920 - CAM_H - DIVIDER_PX}:force_original_aspect_ratio=increase,"
                 f"crop=1080:{1920 - CAM_H - DIVIDER_PX}[bot];"
                 "[top][bot]vstack=inputs=2,setsar=1")
    else:
        graph = ("[0:v]split=2[bg][fg];"
                 "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bgb];"
                 "[fg]scale=1080:-2[fgs];"
                 "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if subs:  # the subtitles filter gets a bare file name: cut_clip runs in the subtitle file's folder
        graph += f",subtitles=filename={Path(subs).name}"
    return graph + "[v]"


def cut_command(src, start: float, end: float, out, ffmpeg=None, subs=None,
                look: str = "full", cam=None) -> list:
    duration = max(1.0, end - start)
    return [*(ffmpeg or [config.FFMPEG]), "-y", "-v", "error",
            "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{duration:.2f}",
            "-filter_complex", _video_graph(subs, look, cam), "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]


def cut_clip(src, start: float, end: float, out, ffmpeg=None, subs=None,
             look: str = "full", cam=None) -> bool:
    """Blocking. True when the clip file exists afterwards. ``subs`` is an .ass file to burn in."""
    cwd = os.path.dirname(os.path.abspath(subs)) if subs else None
    subprocess.run(cut_command(os.path.abspath(src), start, end, os.path.abspath(out), ffmpeg, subs, look, cam),
                   check=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
