"""Who is live right now: Twitch (Helix) and YouTube (Streamlink). See docs/architecture/live-clipper.md."""
import json
import subprocess

import httpx

from . import config

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


def is_youtube(login: str) -> bool:
    """Twitch logins never contain a dot, so a dotted name is a YouTube @handle."""
    return "." in login


def youtube_live(login: str) -> bool:
    """Blocking. Streamlink resolves a live YouTube stream to a list of qualities; offline it fails."""
    try:
        proc = subprocess.run([config.STREAMLINK, "--json", youtube_url(login)],
                              capture_output=True, text=True, timeout=90)
        return proc.returncode == 0 and bool(json.loads(proc.stdout).get("streams"))
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return False  # unreachable or unreadable counts as not live; the next poll checks again


def live_logins(payload: dict) -> set:
    """Lower-cased logins of the channels Helix reports as live."""
    return {s["user_login"].lower() for s in payload.get("data", []) if s.get("type") == "live"}


def newly_live(before: set, now: set) -> set:
    """Channels that went live since the last check: these start a session."""
    return now - before


async def app_token(client: httpx.AsyncClient, client_id: str, secret: str) -> str:
    resp = await client.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": secret, "grant_type": "client_credentials",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


async def fetch_live(client: httpx.AsyncClient, logins, client_id: str, token: str) -> set:
    logins = list(logins)
    if not logins:
        return set()
    resp = await client.get(
        f"{HELIX}/streams",
        params=[("user_login", login) for login in logins[:100]],  # Helix takes up to 100
        headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return live_logins(resp.json())


def youtube_url(login: str) -> str:
    return f"https://www.youtube.com/@{login}/live"


def stream_url(login: str) -> str:
    return youtube_url(login) if is_youtube(login) else f"https://twitch.tv/{login}"
