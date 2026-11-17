"""Discord webhook messages: a stream went live, and a clip is ready to review."""
from urllib.parse import urlparse

import httpx

_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


def valid_webhook(url: str) -> bool:
    """Only https Discord webhook URLs. Anything else would send our messages to an arbitrary host."""
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    return p.scheme == "https" and (p.hostname or "") in _HOSTS and p.path.startswith("/api/webhooks/")


async def send(client: httpx.AsyncClient, url: str, content: str) -> bool:
    if not valid_webhook(url):
        return False
    resp = await client.post(url, json={"content": content[:1900]})  # Discord caps at 2000
    return resp.status_code in (200, 204)
