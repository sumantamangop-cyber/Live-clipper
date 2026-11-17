"""Integration and end-to-end tests for the live clipper, against real ffmpeg on a synthetic stream.

The synthetic chunk is 30 s of video with a quiet bed and two loud beeps (at 5 s and 20 s).
Skipped when ffmpeg or ffprobe is not on PATH (CI without media tools).
"""
import asyncio
import functools
import json
import math
import shutil
import struct
import subprocess
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from live_clipper import config, pipeline, publish, recorder, server, signals, store, cutter

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe"
)


def _sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def chunk(tmp_path):
    """A 30 s synthetic recording: colour bars plus a quiet tone with two loud beeps."""
    out = tmp_path / "chunk.ts"
    wav = tmp_path / "bed.wav"
    rate = 16000
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(30 * rate):
            t = n / rate
            loud = 5 <= t < 6 or 20 <= t < 21
            amp = 0.9 * math.sin(2 * math.pi * 880 * t) if loud else 0.01 * math.sin(2 * math.pi * 220 * t)
            frames += struct.pack("<h", int(amp * 32767))
        w.writeframes(bytes(frames))
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=30",
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration",
         "-of", "json", str(path)], check=True, capture_output=True, text=True)
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(stream.get("duration", 0))


# --- ffmpeg measurements ---------------------------------------------------- #
def test_measure_levels_finds_the_beeps(chunk):
    levels = recorder.measure_levels(chunk)
    assert len(levels) >= 25  # one value per second
    events = signals.audio_events(levels)
    times = sorted(round(t) for t, _ in events)
    assert 5 in times and 20 in times
    assert all(t not in times for t in (12, 13, 14))  # quiet stretch is not an event


def test_cut_clip_produces_a_vertical_1080x1920_file(chunk, tmp_path):
    out = tmp_path / "clip.mp4"
    assert cutter.cut_clip(chunk, 0, 12, out)
    width, height, duration = _probe(out)
    assert (width, height) == (1080, 1920)
    assert 10 < duration < 13


def test_record_chunk_uses_streamlink_stand_in(tmp_path):
    """A fake streamlink that writes the file the way the real one would."""
    fake = tmp_path / "fake_streamlink.py"
    fake.write_text(
        "import sys\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "open(out, 'wb').write(b'x' * 100)\n", encoding="utf-8")
    import sys
    dest = tmp_path / "0000.ts"
    assert recorder.record_chunk("https://twitch.tv/x", dest, 600, streamlink=[sys.executable, str(fake)])
    assert dest.stat().st_size == 100


# --- pipeline on a real chunk ----------------------------------------------- #
def test_process_chunk_cuts_queues_and_links_the_clip(chunk, data_dir):
    made = pipeline.process_chunk(chunk, offset=0.0, chat_stamps=[], channel="alpha")
    assert made, "the two beeps should produce at least one clip"
    clip = made[0]
    assert clip["status"] == "pending" and clip["channel"] == "alpha"
    assert clip["start"] == 0.0  # 5 s minus the 40 s pre-roll clamps to the start of the chunk
    width, height, _ = _probe(data_dir / "clips" / clip["file"])
    assert (width, height) == (1080, 1920)
    assert store.list_clips("pending")[0]["id"] == clip["id"]


def test_process_chunk_respects_the_offset(chunk, data_dir):
    made = pipeline.process_chunk(chunk, offset=600.0, chat_stamps=[], channel="alpha")
    assert made and all(c["start"] >= 0 for c in made)
    assert all(c["end"] >= 600 for c in made)  # every window sits in the second chunk's stream time


# --- end to end: session -> queue -> notify -> approve -> publish ------------ #
@_sync
async def test_end_to_end_session_then_approve_publishes(chunk, data_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")
    calls = {"record": 0, "notes": []}

    def record(url, out):
        calls["record"] += 1
        out.write_bytes(chunk.read_bytes())  # stand in for Streamlink writing a chunk
        return True

    checks = iter([True, False])

    async def live_check(login):
        return next(checks)

    async def notifier(text):
        calls["notes"].append(text)

    processed = await pipeline.run_session(
        "alpha", record=record, live_check=live_check, notifier=notifier,
        chunk_dir=tmp_path / "rec", use_chat=False, retry_seconds=0)
    assert processed == 2 and calls["record"] == 2
    pending = store.list_clips("pending")
    assert pending, "the synthetic beeps must produce reviewable clips"
    assert len(calls["notes"]) == len(pending)  # one Discord note per clip queued

    published = {}

    async def fake_publish(client, key, profile, file_path, title, platforms=publish.DEFAULT_PLATFORMS):
        published.update(key=key, profile=profile, file=file_path, title=title)
        return {"request_id": "req-1"}

    monkeypatch.setattr(publish, "publish", fake_publish)
    client = TestClient(server.app)
    clip_id = pending[0]["id"]
    r = client.post(f"/api/clips/{clip_id}/approve")
    assert r.status_code == 200 and r.json()["status"] == "publishing"
    done = next(c for c in client.get("/api/clips").json() if c["id"] == clip_id)
    assert done["status"] == "published" and done["result"] == {"request_id": "req-1"}
    assert published["key"] == "KEY" and published["profile"] == "creator"
    assert published["file"].endswith(pending[0]["file"])
    assert client.post(f"/api/clips/{clip_id}/approve").status_code == 409  # never posted twice


def test_failed_publish_stays_in_queue_with_reason(chunk, data_dir, monkeypatch):
    made = pipeline.process_chunk(chunk, 0.0, [], "alpha")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")

    async def boom(*args, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(publish, "publish", boom)
    client = TestClient(server.app)
    client.post(f"/api/clips/{made[0]['id']}/approve")
    clip = next(c for c in client.get("/api/clips").json() if c["id"] == made[0]["id"])
    assert clip["status"] == "failed" and "network down" in clip["error"]
