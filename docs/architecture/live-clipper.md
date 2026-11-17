# Live clipper (`live_clipper/`)

Clips live streams as they happen, for review, then posts the approved ones. It is
self-host (BYOK) and runs as its own process, separate from the VOD pipeline in `app.py`.

Source: a public TikTok walkthrough of a "GTA clipping system". Its steps, and where
each one lives here:

| # | Step in the walkthrough | Here | Status |
|---|---|---|---|
| 1 | Poll each platform's API for who is live, every couple of minutes | `watch.py` (Twitch Helix), `pipeline.run_watch` | Twitch built. Kick and YouTube live: not built |
| 2 | Start recording as soon as a streamer goes live | `pipeline.tick` starts `run_session` | Built |
| 3 | Record with Streamlink in 10-minute chunks | `recorder.record_command`, `config.CHUNK_SECONDS` | Built |
| 4 | Sentiment: laughter, chat activity, emoji spikes | `signals.audio_events` (loudness above the clip's median), `signals.chat_events` (chat per 10 s bucket above a relative and an absolute floor) | Built. No emoji or laughter model: loudness and chat rate stand in for them |
| 5 | Clip 20 s before the spike to catch the build-up, 10 s after; clips 25-50 s, 60 s hard cap | `config.PRE_ROLL_SECONDS = 20`, `POST_ROLL_SECONDS = 10`, `MIN_CLIP_SECONDS = 25`, `MAX_CLIP_SECONDS = 60` | Built |
| 6 | Push the clip to Discord, watch it, approve | `notify.py` (Discord webhook: live and clip-ready messages), `static/index.html` (review GUI) | Built. Approval is in the GUI, not a Discord button (a webhook cannot take button clicks) |
| 7 | On approve, post to every social account | `publish.py` through Upload-Post, the service the main app already uses | Built. Upload-Post stands in for Post for Me |
| 8 | Run it across as many accounts as you like | One `UPLOAD_POST_PROFILE` per process | Single account only. Multi-account is a follow-up |

## Flow

```
watch (Helix, every POLL_SECONDS)
  └─ channel goes live ─► run_session
        loop: Streamlink records a 10-min chunk  ──┐
              chat counter (anonymous IRC, times only, no text)
              process_chunk:
                 ffmpeg loudness per second ─┐
                 chat rate per 10 s bucket ──┴► candidates (merge, rank, top 3)
                 each ─► cut 9:16 clip ─► queue (pending) ─► Discord note
        stop when the channel goes offline
review GUI (serve): Approve ─► Upload-Post ─► published | failed (kept, with reason)
```

## Design choices

- **Local review by default.** The GUI and API bind to `127.0.0.1`. Approving publishes,
  so anyone who can reach the server can publish. `serve --lan` binds to `0.0.0.0` and
  refuses to start unless `LIVE_CLIPPER_TOKEN` is set. With a token set, every request
  needs it: `?key=<token>` once, then an HttpOnly cookie. A wrong or missing token gets 403.
- **Chat is counted, never stored.** The IRC reader keeps the time of each message only.
- **Discord only gets to be a notifier.** `notify.valid_webhook` accepts https URLs on
  Discord hosts and nothing else, so a bad config cannot send messages elsewhere.
- **Failures keep the clip.** A failed publish moves the clip to `failed` with the error;
  it is not dropped.
- **Shortcut:** a window that starts before its chunk is cut from the chunk start, so
  its pre-roll is shorter. Upgrade by cutting from the joined recording.
- **Shortcut:** chat time is measured from when the session started, and chunk offsets
  assume chunks are back to back. A stream that Streamlink restarts mid-chunk drifts by
  seconds. Upgrade by stamping chunks with their real start time.

## Testing pyramid

Run from the repo root:

```bash
pytest tests/test_live_clipper_units.py tests/test_live_clipper_server.py -v   # no ffmpeg needed
pytest tests/test_live_clipper_pipeline.py -v                                  # needs ffmpeg + ffprobe
```

- **Unit (fast, pure, mocked HTTP):** Helix parsing and live transitions, the ffmpeg
  loudness parser, spike and window maths, command lines for Streamlink and ffmpeg, IRC
  line handling, webhook validation, the JSON stores, Upload-Post request shape, the poll
  state machine. `tests/test_live_clipper_units.py`, 34 tests.
- **Integration (real ffmpeg, a synthetic chunk with a quiet bed and two loud beeps):**
  loudness measurement finds the beeps, the cut produces a 1080x1920 file of the right
  length, a stand-in Streamlink writes a chunk, the review API approves, publishes,
  refuses a second approval, and keeps failed publishes. `tests/test_live_clipper_server.py`
  covers the HTTP surface without media.
- **End to end:** `run_session` records two chunks, queues clips with one notice each,
  then approve publishes the right file with the right key and profile, and the clip shows
  as `published`. `tests/test_live_clipper_pipeline.py::test_end_to_end_session_then_approve_publishes`.
- **Manual check:** the review GUI was loaded in a browser with two seeded clips: both
  cards rendered, both videos loaded at 1080x1920, no horizontal overflow.

Not covered by tests: a live Twitch stream, real Streamlink against a real channel, real
Discord and Upload-Post calls. Those need the credentials below.

## Configuration

| Variable | Needed for |
|---|---|
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | `watch` (live detection) |
| `DISCORD_WEBHOOK_URL` | live and clip-ready notices (optional) |
| `UPLOAD_POST_API_KEY`, `UPLOAD_POST_PROFILE` | approve and publish |
| `STREAMLINK_BIN`, `FFMPEG_BIN` | only if not on PATH |
| `LIVE_CLIPPER_TOKEN` | Required by `serve --lan`; gates every request when set |
| `LIVE_CLIPPER_DIR` | where recordings, clips and the queue go (default `live_clipper_data/`, git-ignored) |

```bash
pip install streamlink                       # the recorder
python -m live_clipper add some_channel      # watch list
python -m live_clipper watch                 # terminal 1: detect, record, clip
python -m live_clipper serve                 # terminal 2: review at http://127.0.0.1:8765
```
