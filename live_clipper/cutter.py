"""Cut a window from a recording into a 9:16 clip (1080x1920).

The whole frame stays visible: it sits centred over a blurred copy of itself. A centre crop
cut the streamer's face out of 16:9 streams, so it is not used.
"""
import os
import subprocess
from pathlib import Path

from . import config


def _video_graph(subs) -> str:
    graph = ("[0:v]split=2[bg][fg];"
             "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bgb];"
             "[fg]scale=1080:-2[fgs];"
             "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if subs:  # the subtitles filter gets a bare file name: cut_clip runs in the subtitle file's folder
        graph += f",subtitles=filename={Path(subs).name}:force_style='{config.CAPTION_STYLE}'"
    return graph + "[v]"


def cut_command(src, start: float, end: float, out, ffmpeg=None, subs=None) -> list:
    duration = max(1.0, end - start)
    return [*(ffmpeg or [config.FFMPEG]), "-y", "-v", "error",
            "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{duration:.2f}",
            "-filter_complex", _video_graph(subs), "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]


def cut_clip(src, start: float, end: float, out, ffmpeg=None, subs=None) -> bool:
    """Blocking. True when the clip file exists afterwards. ``subs`` is an .srt file to burn in."""
    cwd = os.path.dirname(os.path.abspath(subs)) if subs else None
    subprocess.run(cut_command(os.path.abspath(src), start, end, os.path.abspath(out), ffmpeg, subs),
                   check=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
