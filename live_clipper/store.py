"""The review queue and the watch list: two small JSON files under DATA_DIR, written atomically."""
import json
import os
import re
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


def facecam(channel: str):
    """The facecam box (x, y, w, h) in source pixels for a channel, from facecams.json; None if unset."""
    box = _read("facecams.json", {}).get(channel)
    return (box["x"], box["y"], box["w"], box["h"]) if box else None


def add_clip(channel: str, start: float, end: float, score: float, file: str,
             zoom_file: str = None, gaming_file: str = None) -> dict:
    clip = {
        "id": uuid.uuid4().hex, "channel": channel, "start": start, "end": end,
        "score": score, "file": file, "zoom_file": zoom_file, "gaming_file": gaming_file, "status": "pending",
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


def paused() -> list:
    """Channels the user switched off: they stay on the watch list but are not recorded."""
    return _read("paused.json", [])


def set_recording(login: str, on: bool) -> list:
    with _lock:
        off = set(_read("paused.json", []))
        if on:
            off.discard(login)
        else:
            off.add(login)
        _write("paused.json", sorted(off))
    return sorted(off)


def write_watch_state(state: dict) -> None:
    """The watcher writes what it sees and records; the review page reads it."""
    with _lock:
        _write("watch_state.json", state)


def watch_state() -> dict:
    return _read("watch_state.json", {"live": [], "recording": [], "updated": 0})


def add_channel(login: str) -> list:
    login = login.strip().lower().removeprefix("@")
    if "." in login:  # a YouTube @handle (watch.is_youtube); Twitch logins never contain a dot
        valid = re.fullmatch(r"[a-z0-9._-]{2,30}", login) is not None  # YouTube resolves @dylan.page. but not @dylan.page
    else:
        valid = login.replace("_", "").isalnum() and len(login) <= 25
    if not valid:
        raise ValueError("not a Twitch login or YouTube @handle")
    with _lock:
        chans = _read("channels.json", [])
        if login not in chans:
            chans.append(login)
            _write("channels.json", chans)
    return chans
