"""Review GUI and JSON API. Local by default: approving a clip publishes it, so there is no open access."""
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, publish, store

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Live clipper")


class Channel(BaseModel):
    login: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/clips")
def clips():
    return sorted(store.list_clips(), key=lambda c: c["created_at"], reverse=True)


@app.get("/clips/{name}")
def clip_file(name: str):
    if Path(name).name != name or not name.endswith(".mp4"):
        raise HTTPException(400, "bad clip name")
    path = config.DATA_DIR / "clips" / name
    if not path.exists():
        raise HTTPException(404, "no such clip")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/channels")
def channels():
    return store.channels()


@app.post("/api/channels")
def add_channel(body: Channel):
    try:
        return store.add_channel(body.login)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    if not (config.UPLOAD_POST_API_KEY and config.UPLOAD_POST_PROFILE):
        raise HTTPException(503, "publishing is not configured: set UPLOAD_POST_API_KEY and UPLOAD_POST_PROFILE")
    clip = _clip(clip_id)
    if clip["status"] != "pending":
        raise HTTPException(409, f"clip is {clip['status']}")
    updated = store.set_status(clip_id, "publishing")
    tasks.add_task(_publish, clip_id, clip["file"], f"{clip['channel']} highlight")
    return updated


@app.post("/api/clips/{clip_id}/reject")
def reject(clip_id: str):
    clip = _clip(clip_id)
    if clip["status"] != "pending":
        raise HTTPException(409, f"clip is {clip['status']}")
    return store.set_status(clip_id, "rejected")
