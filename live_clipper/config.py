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
ACCESS_TOKEN = os.environ.get("LIVE_CLIPPER_TOKEN", "")  # required when serving to the LAN (serve --lan)
UPLOAD_POST_API_KEY = os.environ.get("UPLOAD_POST_API_KEY", "")
UPLOAD_POST_PROFILE = os.environ.get("UPLOAD_POST_PROFILE", "")

STREAMLINK = os.environ.get("STREAMLINK_BIN", "streamlink")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
CAPTION_STYLE = os.environ.get(  # libass style for the burned-in speech captions
    "CLIP_CAPTION_STYLE", "FontName=Impact,Bold=1,FontSize=13,Outline=3,Shadow=0,Alignment=2,MarginV=60")

REPO_ROOT = Path(__file__).resolve().parent.parent
BUZZWORDS_FILE = Path(os.environ.get("BUZZWORDS_FILE", REPO_ROOT / "tripwire" / "buzzwords.yml"))
BUZZWORDS_ENABLED = os.environ.get("BUZZWORDS_ENABLED", "1") == "1"  # 0 skips transcription entirely

CHUNK_SECONDS = 600          # recording is cut into 10-minute files
HOT_CHAT_MESSAGES = int(os.environ.get("HOT_CHAT_MESSAGES", "30"))  # chat messages per minute that start a recording
PRE_ROLL_SECONDS = 40        # the build-up before a spike (the video says 35-45 s)
POST_ROLL_SECONDS = 15       # the reaction after it
POLL_SECONDS = 120           # how often live status is checked
MAX_CLIPS_PER_CHUNK = 3
MIN_CLIP_SECONDS = 40        # clips run 40-60 s: shorter windows are padded forward to this
MIN_CLIP_SCORE = float(os.environ.get("MIN_CLIP_SCORE", "25"))  # below this a window is dead air, not queued
