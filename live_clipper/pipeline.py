"""The loop that ties the modules together: live -> record -> moments -> clips -> review -> Discord."""
import asyncio
import sys
import time
import uuid
from pathlib import Path

import httpx

from . import buzz, captions
from . import chat as chat_mod
from . import config, cutter, notify, recorder, signals, store, watch


def process_chunk(chunk_path, offset: float, chat_stamps, channel: str) -> list:
    """Blocking. Find the moments in one recorded chunk and cut each into a clip for review.

    ``offset`` is the chunk's start in stream seconds; chat stamps are stream seconds too.
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
    for c in windows:
        is_loud = any(c["start"] <= t <= c["end"] for t in loud)
        why = ("kept" if is_loud and c["score"] >= config.MIN_CLIP_SCORE
               else "score below MIN_CLIP_SCORE" if c["score"] < config.MIN_CLIP_SCORE else "no sound/speech trigger")
        parts = {}
        for t, w, src in tagged:
            if c["start"] <= t <= c["end"]:
                n, total = parts.get(src, (0, 0.0))
                parts[src] = (n + 1, total + w)
        made_of = ", ".join(f"{n} {src} (+{total:.1f})" for src, (n, total) in parts.items()) or "nothing"
        print(f"  window {c['start']:.0f}-{c['end']:.0f}s score {c['score']:.1f}: {why}; from {made_of}",
              file=sys.stderr)
    windows = [c for c in windows if c["score"] >= config.MIN_CLIP_SCORE
               and any(c["start"] <= t <= c["end"] for t in loud)]
    made = []
    for c in windows[:config.MAX_CLIPS_PER_CHUNK]:
        start = max(0.0, c["start"] - offset)
        stop = min(float(config.CHUNK_SECONDS), c["end"] - offset)
        if stop - start < config.MIN_CLIP_SECONDS:  # pad forward so every clip is 40-60 s
            stop = min(float(config.CHUNK_SECONDS), start + config.MIN_CLIP_SECONDS)
        if stop - start < config.MIN_CLIP_SECONDS:
            continue
        name = f"{channel}_{uuid.uuid4().hex[:10]}.mp4"
        subs = None
        if words:
            subs = clips_dir / f"{name}.srt"
            if not captions.write_srt(words, start, stop, subs):
                subs = None
        try:
            cutter.cut_clip(chunk_path, start, stop, clips_dir / name, subs=subs)
        finally:
            if subs:
                subs.unlink(missing_ok=True)
        made.append(store.add_clip(channel, c["start"], c["end"], c["score"], name))
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
                try:
                    clips = await asyncio.to_thread(process_chunk, out, index * config.CHUNK_SECONDS,
                                                    list(counter.stamps), login)
                except Exception as e:  # keep the session alive; the chunk stays on disk to re-run
                    print(f"process_chunk failed for {out}: {e}", file=sys.stderr)
                    clips = []
                else:
                    out.unlink(missing_ok=True)  # clips are cut; the chunk itself is no longer needed
                if notifier:
                    for clip in clips:
                        await notifier(f"Clip ready for review from {login}: {clip['file']} (score {clip['score']})")
            index += 1
            if live_check and not await live_check(login):
                break
            if not ok:
                await asyncio.sleep(retry_seconds)
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


async def run_watch(poll: float = config.POLL_SECONDS):
    """Poll Twitch for every channel on the watch list and record the ones that go live."""
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        raise RuntimeError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set to watch Twitch")
    running, before = {}, set()
    async with httpx.AsyncClient(timeout=20) as client:
        token = None

        async def check(login):
            return login in await watch.fetch_live(client, [login], config.TWITCH_CLIENT_ID, token)

        async def notifier(text):
            await notify.send(client, config.DISCORD_WEBHOOK_URL, text)

        def start(login):
            return asyncio.create_task(run_session(login, live_check=check, notifier=notifier))

        while True:
            try:
                if token is None:
                    token = await watch.app_token(client, config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET)
                live = await watch.fetch_live(client, store.channels(), config.TWITCH_CLIENT_ID, token)
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
            await asyncio.sleep(poll)
