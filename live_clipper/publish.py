"""Post an approved clip through Upload-Post (the same service the main app uses)."""
import os

import httpx

UPLOAD_URL = "https://api.upload-post.com/api/upload"
DEFAULT_PLATFORMS = ("tiktok", "instagram", "youtube")


async def publish(client: httpx.AsyncClient, api_key: str, profile: str, file_path: str,
                  title: str, platforms=DEFAULT_PLATFORMS) -> dict:
    if not api_key or not profile:
        raise RuntimeError("UPLOAD_POST_API_KEY and UPLOAD_POST_PROFILE must be set to publish")
    data = {"user": profile, "title": title[:100], "platform[]": list(platforms), "async_upload": "true"}
    with open(file_path, "rb") as f:
        files = {"video": (os.path.basename(file_path), f.read(), "video/mp4")}
    resp = await client.post(UPLOAD_URL, headers={"Authorization": f"Apikey {api_key}"},
                             data=data, files=files, timeout=300)
    resp.raise_for_status()
    return resp.json()
