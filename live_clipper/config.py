"""Settings for the live clipper, read from the environment.

Nothing here is a default credential: every secret is empty until the operator
sets it, and the features that need one report that they are not configured.
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("LIVE_CLIPPER_DIR", "live_clipper_data"))

TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
UPLOAD_POST_API_KEY = os.environ.get("UPLOAD_POST_API_KEY", "")
UPLOAD_POST_PROFILE = os.environ.get("UPLOAD_POST_PROFILE", "")

STREAMLINK = os.environ.get("STREAMLINK_BIN", "streamlink")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

CHUNK_SECONDS = 600          # recording is cut into 10-minute files
PRE_ROLL_SECONDS = 40        # the build-up before a spike (the video says 35-45 s)
POST_ROLL_SECONDS = 15       # the reaction after it
POLL_SECONDS = 120           # how often live status is checked
MAX_CLIPS_PER_CHUNK = 3
