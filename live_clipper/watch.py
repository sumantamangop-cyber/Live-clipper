"""Who is live right now. Twitch only for now (Helix); see docs/architecture/live-clipper.md."""
import httpx

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


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


def stream_url(login: str) -> str:
    return f"https://twitch.tv/{login}"
