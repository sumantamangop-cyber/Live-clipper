"""The loop that ties the modules together: live -> record -> moments -> clips -> review -> Discord."""
import asyncio
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from . import buzz, captions
from . import chat as chat_mod
from . import config, cutter, notify, recorder, signals, store, watch


def process_chunk(chunk_path, offset: float, chat_stamps, channel: str, notes: list = None) -> list:
    """Blocking. Find the moments in one recorded chunk and cut each into a clip for review.

    ``offset`` is the chunk's start in stream seconds; chat stamps are stream seconds too.
    ``notes`` (optional) gets one line saying why the chunk made no clip, for a Discord notice.
    shortcut: a window that starts before its chunk is cut from the chunk start, so its
    pre-roll is shorter; upgrade by cutting from the joined recording.
    """
    end = offset + config.CHUNK_SECONDS
    levels = recorder.measure_levels(chunk_path)
    tagged = [(offset + t, w, "loud") for t, w in signals.audio_events(levels)]  # (time, weight, source)
    words = []  # the transcript drives both the buzzword triggers and the speech captions
    if config.BUZZWORDS_ENABLED:  # shortcut: transcribes every chunk on CPU, slow on long chunks; upgrade with GPU or fewer chunks
        cfg, tiers, rules = buzz.load_rules(channel)
        words = buzz.transcribe_words(chunk_path)
        hits = buzz.find_hits(words, rules)
        tagged += [(offset + t, w, "buzzword") for t, w in buzz.events(hits, buzz.rise_per_second(levels), cfg, tiers)]
    # a window needs a sound or spoken trigger: chat alone is often spam, not a moment
    loud = [t for t, _, _ in tagged]
    tagged += [(s, w, "chat") for s, w in signals.chat_events([s for s in chat_stamps if offset <= s < end])]
    events = [(t, w) for t, w, _ in tagged]
    clips_dir = config.DATA_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    windows = signals.candidates(events, config.PRE_ROLL_SECONDS, config.POST_ROLL_SECONDS,
                                 limit=len(events), max_len=config.MAX_CLIP_SECONDS)
    # log why each window was kept or dropped, and which triggers made up its score
    print(f"{channel} chunk at {offset:.0f}s: {len(loud)} sound/speech triggers, "
          f"{len(events) - len(loud)} chat triggers, {len(windows)} windows", file=sys.stderr)
    best = None  # the highest-scoring window and why it was not enough, for the no-clip notice
    for c in windows:
        is_loud = any(c["start"] <= t <= c["end"] for t in loud)
        rate = clip_rate(c)
        why = ("kept" if is_loud and rate >= config.MIN_CLIP_RATE
               else "score below MIN_CLIP_RATE" if rate < config.MIN_CLIP_RATE else "no sound/speech trigger")
        if best is None or rate > best[0]:
            best = (rate, f"best window {c['start']:.0f}-{c['end']:.0f}s scored {rate:.2f} per second: {why}")
        parts = {}
        for t, w, src in tagged:
            if c["start"] <= t <= c["end"]:
                n, total = parts.get(src, (0, 0.0))
                parts[src] = (n + 1, total + w)
        made_of = ", ".join(f"{n} {src} (+{total:.1f})" for src, (n, total) in parts.items()) or "nothing"
        print(f"  window {c['start']:.0f}-{c['end']:.0f}s score {c['score']:.1f}: {why}; from {made_of}",
              file=sys.stderr)
    windows = [c for c in windows if clip_rate(c) >= config.MIN_CLIP_RATE
               and any(c["start"] <= t <= c["end"] for t in loud)]
    windows.sort(key=clip_rate, reverse=True)  # the most intense moments first, not the longest
    made = []
    for c in windows[:config.MAX_CLIPS_PER_CHUNK]:
        start = max(0.0, c["start"] - offset)
        stop = min(float(config.CHUNK_SECONDS), c["end"] - offset)
        if stop - start < config.MIN_CLIP_SECONDS:  # pad forward so every clip is 40-60 s
            stop = min(float(config.CHUNK_SECONDS), start + config.MIN_CLIP_SECONDS)
        if stop - start < config.MIN_CLIP_SECONDS:
            continue
        stem = f"{channel}_{uuid.uuid4().hex[:10]}"
        cam = store.facecam(channel)  # the gaming look exists only where the facecam box is known
        looks = ["full", "zoom"] + (["gaming"] if cam else [])
        files = {look: f"{stem}_{look}.mp4" for look in looks}
        files["full"] = f"{stem}.mp4"  # the approved file keeps the plain name
        subs = {}  # look -> ASS file: captions sit at the bottom, or under the divider for gaming
        if words:
            for look in ("bottom", "top"):
                path = clips_dir / f"{stem}_{look}.ass"
                if captions.write_ass(words, start, stop, path, top=look == "top"):
                    subs[look] = path
        made_files = {}
        try:
            for look in looks:
                ass = subs.get("top" if look == "gaming" else "bottom")
                try:
                    cutter.cut_clip(chunk_path, start, stop, clips_dir / files[look], subs=ass,
                                    look=look, cam=cam)
                except Exception as e:
                    if look == "full":
                        raise
                    # an extra look is optional: a failed one is dropped, the full frame is kept
                    print(f"{look} cut failed for {files[look]}: {e!r}", file=sys.stderr)
                    (clips_dir / files[look]).unlink(missing_ok=True)  # never leave a half-written file
                    continue
                made_files[look] = files[look]
        finally:
            for path in subs.values():
                path.unlink(missing_ok=True)
        made.append(store.add_clip(channel, c["start"], c["end"], c["score"], made_files["full"],
                                   zoom_file=made_files.get("zoom"), gaming_file=made_files.get("gaming")))
    if notes is not None and not made:
        notes.append(best[1] if best else "no moments found: no sound, speech or chat spike")
    return made


async def run_session(login: str, *, record=None, live_check=None, notifier=None,
                      chunk_dir=None, use_chat: bool = True, retry_seconds: float = 30,
                      max_chunks: int = None, is_hot=None, poll_seconds: float = 15) -> int:
    """Watch a live channel's chat and record chunks only while it is busy. Returns chunks recorded.

    Recording starts when chat is hot (``signals.is_hot``) and carries on chunk by chunk
    while it stays hot. Quiet chat costs one chat connection and a live check per poll.
    shortcut: the moments before the first hot minute are never recorded, so the first
    pre-roll is lost; upgrade by recording always and deleting quiet chunks.
    """
    record = record or (lambda url, out: recorder.record_chunk(url, out))
    is_hot = is_hot or (lambda stamps, now: signals.is_hot(stamps, now, min_msgs=config.HOT_CHAT_MESSAGES))
    # one folder per session, so a restarted watcher never overwrites an earlier recording
    chunk_dir = Path(chunk_dir or config.DATA_DIR / "recordings" / f"{login}_{int(time.time())}")
    chunk_dir.mkdir(parents=True, exist_ok=True)
    counter = chat_mod.ChatCounter(login)
    chat_task = counter.start() if use_chat else None
    url = watch.stream_url(login)
    index = 0
    processing = []  # chunks being cut in the background, so recording never waits for them

    async def cut_chunk(out, i, stamps):  # keep the session alive; a failed chunk stays on disk to re-run
        notes = []
        try:
            clips = await asyncio.to_thread(process_chunk, out, i * config.CHUNK_SECONDS, stamps, login, notes)
        except Exception as e:
            print(f"process_chunk failed for {out}: {e}", file=sys.stderr)
            return
        out.unlink(missing_ok=True)  # clips are cut; the chunk itself is no longer needed
        if notifier:
            for clip in clips:
                await notifier(f"Clip ready for review from {login}: {clip['file']} (score {clip['score']})")
            if not clips:  # a chunk that made nothing still says why, so you know it was checked
                await notifier(f"No clip from {login} at {i * config.CHUNK_SECONDS // 60} min: {notes[0]}")

    try:
        while max_chunks is None or index < max_chunks:
            if not is_hot(counter.stamps, time.monotonic() - counter.t0):
                await asyncio.sleep(poll_seconds)
                if live_check and not await live_check(login):
                    break
                continue
            out = chunk_dir / f"{index:04d}.ts"
            ok = await asyncio.to_thread(record, url, out)
            if ok:
                processing.append(asyncio.create_task(cut_chunk(out, index, list(counter.stamps))))
            index += 1
            if live_check and not await live_check(login):
                break
            if not ok:
                await asyncio.sleep(retry_seconds)
        await asyncio.gather(*processing)  # a normal end waits for the last clips to be queued
    finally:
        if chat_task:
            counter.stop()
    return index


def tick(running: dict, live: set, start, paused=(), cap: int = config.MAX_RECORDINGS) -> None:
    """One poll: forget finished sessions, stop switched-off ones, start live channels up to ``cap``.

    ``start(login)`` must return an asyncio task. Mutates ``running``.
    shortcut: a stopped recording's current 10-minute chunk keeps going in its thread and is
    left on disk; upgrade with a stop flag that the recorder checks.
    """
    for login in [k for k, t in running.items() if t.done()]:
        del running[login]
    for login in [k for k in running if k in paused]:
        running.pop(login).cancel()
    for login in sorted(live - set(running) - set(paused)):
        if len(running) >= cap:
            break
        running[login] = start(login)


def clip_rate(c) -> float:
    """Score per second of a window: a short burst is judged by its intensity, not its length."""
    return c["score"] / max(1.0, c["end"] - c["start"])


def stale_pending(clips, now: float, after_s: float) -> list:
    """Pending clips made more than ``after_s`` seconds before ``now`` (a unix time)."""
    return [c for c in clips if now - datetime.fromisoformat(c["created_at"]).timestamp() > after_s]


async def run_watch(poll: float = config.POLL_SECONDS):
    """Poll Twitch for every channel on the watch list and record the ones that go live."""
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        raise RuntimeError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set to watch Twitch")
    running, before, reminded = {}, set(), set()
    async with httpx.AsyncClient(timeout=20) as client:
        token = None

        async def check(login):
            if watch.is_youtube(login):
                return await asyncio.to_thread(watch.youtube_live, login)
            return login in await watch.fetch_live(client, [login], config.TWITCH_CLIENT_ID, token)

        async def notifier(text):
            await notify.send(client, config.DISCORD_WEBHOOK_URL, text)

        def start(login):
            if watch.is_youtube(login):  # YouTube has no chat we can count: record while it is live
                return asyncio.create_task(run_session(login, live_check=check, notifier=notifier,
                                                       use_chat=False, is_hot=lambda stamps, now: True))
            return asyncio.create_task(run_session(login, live_check=check, notifier=notifier))

        while True:
            try:
                if token is None:
                    token = await watch.app_token(client, config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET)
                logins = store.channels()
                twitch = [c for c in logins if not watch.is_youtube(c)]
                youtube = [c for c in logins if watch.is_youtube(c)]
                live = await watch.fetch_live(client, twitch, config.TWITCH_CLIENT_ID, token)
                checks = await asyncio.gather(*(asyncio.to_thread(watch.youtube_live, c) for c in youtube))
                live |= {c for c, on in zip(youtube, checks) if on}
                for login in watch.newly_live(before, live):
                    if config.DISCORD_WEBHOOK_URL:
                        await notifier(f"{login} is live. Recording started.")
            except (httpx.HTTPError, OSError) as e:
                # a dropped network or a refused token must not end the watcher: retry on the next poll
                print(f"watch: {e!r}; retrying in {poll}s", file=sys.stderr)
                token = None  # fetch a fresh token next time too
                await asyncio.sleep(poll)
                continue
            before = live
            tick(running, live, start, paused=set(store.paused()))
            store.write_watch_state({"live": sorted(live), "recording": sorted(running),
                                     "updated": time.time()})
            # a clip nobody has reviewed in time gets one Discord reminder (not a crash if Discord is down)
            for c in stale_pending(store.list_clips("pending"), time.time(), config.REVIEW_REMINDER_MINUTES * 60):
                if c["id"] not in reminded:
                    reminded.add(c["id"])
                    try:
                        await notifier(f"Clip still waiting for review: {c['channel']} (score {c['score']}). Approve or reject it.")
                    except httpx.HTTPError as e:
                        print(f"review reminder failed: {e!r}", file=sys.stderr)
            await asyncio.sleep(poll)
