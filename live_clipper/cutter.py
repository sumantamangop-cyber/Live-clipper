"""Cut a window from a recording into a 9:16 clip (centre crop, 1080x1920)."""
import subprocess

from . import config


def cut_command(src, start: float, end: float, out, ffmpeg=None) -> list:
    duration = max(1.0, end - start)
    return [*(ffmpeg or [config.FFMPEG]), "-y", "-v", "error",
            "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{duration:.2f}",
            "-vf", "crop=ih*9/16:ih,scale=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]


def cut_clip(src, start: float, end: float, out, ffmpeg=None) -> bool:
    """Blocking. True when the clip file exists afterwards."""
    subprocess.run(cut_command(src, start, end, out, ffmpeg), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
