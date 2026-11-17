# Live clipper

Watches Twitch channels you choose. When a stream gets busy, it records the
stream, finds the funny or exciting moments, cuts them into vertical 25-50 second
clips, and puts them in a review page. You approve the ones you like and they get
posted. Nothing is posted without your approval.

It runs on your own computer as a separate program from the main OpenShorts app.

## How it works, step by step

1. **Watch.** Every couple of minutes it asks Twitch which channels on your list are
   live.
2. **Listen to chat.** While a channel is live, it counts chat messages. It stores
   only the time of each message, never the text.
3. **Record only when busy.** Recording starts when chat gets 30+ messages in a
   minute (set with `HOT_CHAT_MESSAGES`). Quiet streams cost almost nothing.
4. **Find moments.** Each 10-minute recording is checked for three signals:
   - a sudden jump in loudness (shouting, laughing, reactions),
   - a burst of chat,
   - a buzzword said out loud, such as "clip that" or "no way", but only when the
     audio also gets louder right then. The word list is `tripwire/buzzwords.yml`.
5. **Cut clips.** Each moment becomes a 25-50 second clip (never over a minute) that starts
   a few seconds before the spike. The clip keeps the whole picture (the video sits on a blurred
   copy of itself, so the streamer's face isn't cropped out). Speech is burned in as
   captions.
6. **Review.** The clips appear in a page on your computer. Each has Approve,
   Reject and Download buttons.
7. **Post.** Approving posts the clip through Upload-Post, if you have set it up.
   Without it, approving just marks the clip as approved, and you download and post
   it yourself.

Rejecting a clip deletes its file.

## Setting it up

You need Python, `ffmpeg`, and `streamlink` installed and on your PATH.

```bash
pip install -r requirements.txt
pip install streamlink

python -m live_clipper add some_channel     # add a channel to the watch list
python -m live_clipper watch                # terminal 1: watch, record, clip
python -m live_clipper serve                # terminal 2: review page
```

The review page is at http://127.0.0.1:8765.

## Settings

Set these as environment variables (for example in a `.env` file in the project
folder). Never commit real keys.

| Variable | What it does |
|---|---|
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Needed to detect live channels |
| `UPLOAD_POST_API_KEY`, `UPLOAD_POST_PROFILE` | Needed to post approved clips automatically |
| `DISCORD_WEBHOOK_URL` | Optional: sends a note when a clip is ready |
| `LIVE_CLIPPER_TOKEN` | Password for the review page. Required to view it from other devices |
| `HOT_CHAT_MESSAGES` | Chat messages per minute that start recording (default 30) |
| `BUZZWORDS_ENABLED` | Set to `0` to skip speech detection (faster, no captions) |
| `MIN_CLIP_SCORE` | How strong a moment must be to make a clip (default 25) |
| `LIVE_CLIPPER_DIR` | Where recordings, clips and the queue are kept (default `live_clipper_data/`, not committed) |

## Viewing the review page from your phone

```bash
set LIVE_CLIPPER_TOKEN=pick-a-long-random-string
python -m live_clipper serve --lan
```

Open the address the terminal shows, adding `?key=` and your token the first time.
The `--lan` option won't start without a token, so people on the same Wi-Fi can't
approve clips without it.

## Known limits

- Only Twitch is supported. Kick and YouTube are not.
- Speech detection transcribes every recording on the CPU, which is slow on long
  recordings.
- Recording starts only once chat is busy, so the first few seconds of a moment
  before that can be lost.
- Posting goes to one account per process.

For the design decisions and test layout, see
[docs/architecture/live-clipper.md](../docs/architecture/live-clipper.md).
