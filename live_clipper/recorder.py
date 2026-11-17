"""Record a live stream in fixed-length chunks with Streamlink, and measure each chunk's loudness."""
import os
import subprocess
import tempfile

from . import config, signals


def record_command(url: str, out_path, chunk_seconds: int = config.CHUNK_SECONDS,
                   streamlink=None) -> list:
    """One Streamlink run that stops after ``chunk_seconds`` (or when the stream ends)."""
    return [*(streamlink or [config.STREAMLINK]),
            "--hls-duration", str(chunk_seconds), "--force", "-o", str(out_path),
            url, "best"]


def record_chunk(url: str, out_path, chunk_seconds: int = config.CHUNK_SECONDS, streamlink=None) -> bool:
    """Blocking. True when a non-empty chunk file was written."""
    subprocess.run(record_command(url, out_path, chunk_seconds, streamlink), check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def audio_levels_command(src, ffmpeg=None) -> list:
    """Loudness per one-second frame, printed to a file called levels.txt in the working dir."""
    af = ("aresample=16000,asetnsamples=n=16000:p=0,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=levels.txt")
    return [*(ffmpeg or [config.FFMPEG]), "-v", "error", "-i", str(src), "-vn", "-af", af, "-f", "null", "-"]


def measure_levels(src, ffmpeg=None) -> list:
    """Blocking. ``(second, dB)`` pairs for the chunk's audio, empty if it has none."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(audio_levels_command(os.path.abspath(src), ffmpeg), cwd=tmp,
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path = os.path.join(tmp, "levels.txt")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8", errors="replace") as f:
            return signals.parse_levels(f.read())
