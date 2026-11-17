"""The review queue and the watch list: two small JSON files under DATA_DIR, written atomically."""
import json
import os
import threading
import uuid
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()
STATUSES = {"pending", "approved", "rejected", "publishing", "published", "failed"}


def _path(name: str):
    return config.DATA_DIR / name


def _read(name: str, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _write(name: str, value):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(name).with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, _path(name))


def add_clip(channel: str, start: float, end: float, score: float, file: str) -> dict:
    clip = {
        "id": uuid.uuid4().hex, "channel": channel, "start": start, "end": end,
        "score": score, "file": file, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        clips = _read("clips.json", [])
        clips.append(clip)
        _write("clips.json", clips)
    return clip


def list_clips(status: str = None) -> list:
    clips = _read("clips.json", [])
    return [c for c in clips if status is None or c["status"] == status]


def set_status(clip_id: str, status: str, **extra) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    with _lock:
        clips = _read("clips.json", [])
        for c in clips:
            if c["id"] == clip_id:
                c["status"] = status
                c.update(extra)
                _write("clips.json", clips)
                return c
    raise KeyError(clip_id)


def remove_clip(clip_id: str) -> None:
    with _lock:
        clips = _read("clips.json", [])
        if not any(c["id"] == clip_id for c in clips):
            raise KeyError(clip_id)
        _write("clips.json", [c for c in clips if c["id"] != clip_id])


def channels() -> list:
    return _read("channels.json", [])


def add_channel(login: str) -> list:
    login = login.strip().lower()
    if not login.replace("_", "").isalnum() or len(login) > 25:
        raise ValueError("not a Twitch login")
    with _lock:
        chans = _read("channels.json", [])
        if login not in chans:
            chans.append(login)
            _write("channels.json", chans)
    return chans
