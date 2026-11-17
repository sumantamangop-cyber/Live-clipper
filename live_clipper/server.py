"""Review GUI and JSON API. Local by default: approving a clip publishes it, so there is no open access."""
import hmac
import time
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import config, publish, store

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Live clipper")


@app.middleware("http")
async def require_token(request: Request, call_next):
    """With LIVE_CLIPPER_TOKEN set (needed for LAN access), every request needs it: ?key= once, then a cookie."""
    token = config.ACCESS_TOKEN
    if not token:
        return await call_next(request)
    key = request.query_params.get("key", "")
    cookie = request.cookies.get("lc_key", "")
    if not (hmac.compare_digest(key, token) or hmac.compare_digest(cookie, token)):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    response = await call_next(request)
    if hmac.compare_digest(key, token):
        response.set_cookie("lc_key", token, httponly=True, samesite="strict")
    return response


class Channel(BaseModel):
    login: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/clips")
def clips():
    return sorted(store.list_clips(), key=lambda c: c["created_at"], reverse=True)


@app.get("/clips/{name}")
def clip_file(name: str, request: Request):
    if Path(name).name != name or not name.endswith(".mp4"):
        raise HTTPException(400, "bad clip name")
    path = config.DATA_DIR / "clips" / name
    if not path.exists():
        raise HTTPException(404, "no such clip")
    # phones seek with Range requests; without a 206 reply the player restarts from the beginning
    size = path.stat().st_size
    spec = request.headers.get("range", "").removeprefix("bytes=")
    if not spec:
        return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})
    first, _, last = spec.partition("-")
    try:
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last) if first and last else size - 1, size - 1)
    except ValueError:
        raise HTTPException(416, "bad range")
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(end - start + 1)
    return Response(data, status_code=206, media_type="video/mp4", headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(len(data))})


@app.get("/api/channels")
def channels():
    return store.channels()


@app.post("/api/channels")
def add_channel(body: Channel):
    try:
        return store.add_channel(body.login)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/watch")
def watch_status():
    state = store.watch_state()
    off = set(store.paused())
    return {
        "watcher_running": time.time() - state.get("updated", 0) < 600,  # the watcher writes every poll
        "max": config.MAX_RECORDINGS,
        "recording": len(state.get("recording", [])),
        "channels": [{"login": c, "live": c in state.get("live", []),
                      "recording": c in state.get("recording", []), "on": c not in off}
                     for c in store.channels()],
    }


class Recording(BaseModel):
    on: bool


@app.post("/api/channels/{login}/recording")
def set_recording(login: str, body: Recording):
    if login not in store.channels():
        raise HTTPException(404, "not on the watch list")
    store.set_recording(login, body.on)
    return {"login": login, "on": body.on}


def _clip(clip_id: str) -> dict:
    for c in store.list_clips():
        if c["id"] == clip_id:
            return c
    raise HTTPException(404, "no such clip")


async def _publish(clip_id: str, file: str, title: str):
    try:
        async with httpx.AsyncClient() as client:
            result = await publish.publish(client, config.UPLOAD_POST_API_KEY, config.UPLOAD_POST_PROFILE,
                                           str(config.DATA_DIR / "clips" / file), title)
        store.set_status(clip_id, "published", result=result)
    except Exception as e:  # keep the clip in the queue with the reason, never drop it
        store.set_status(clip_id, "failed", error=str(e)[:300])


@app.post("/api/clips/{clip_id}/approve")
async def approve(clip_id: str, tasks: BackgroundTasks):
    clip = _clip(clip_id)
    if clip["status"] != "pending":
        raise HTTPException(409, f"clip is {clip['status']}")
    if not (config.UPLOAD_POST_API_KEY and config.UPLOAD_POST_PROFILE):
        return store.set_status(clip_id, "approved")  # manual posting: download the file and post it yourself
    updated = store.set_status(clip_id, "publishing")
    tasks.add_task(_publish, clip_id, clip["file"], f"{clip['channel']} highlight")
    return updated


@app.post("/api/clips/{clip_id}/reject")
def reject(clip_id: str):
    clip = _clip(clip_id)
    if clip["status"] != "pending":
        raise HTTPException(409, f"clip is {clip['status']}")
    (config.DATA_DIR / "clips" / clip["file"]).unlink(missing_ok=True)  # a rejected clip is deleted from disk
    store.remove_clip(clip_id)
    return {"removed": clip_id}
