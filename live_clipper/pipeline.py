"""The loop that ties the modules together: live -> record -> moments -> clips -> review -> Discord."""
import asyncio
import uuid
from pathlib import Path

import httpx

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
    events = [(offset + t, w) for t, w in signals.audio_events(levels)]
    events += signals.chat_events([s for s in chat_stamps if offset <= s < end])
    clips_dir = config.DATA_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for c in signals.candidates(events, config.PRE_ROLL_SECONDS, config.POST_ROLL_SECONDS,
                                config.MAX_CLIPS_PER_CHUNK):
        start = max(0.0, c["start"] - offset)
        stop = min(float(config.CHUNK_SECONDS), c["end"] - offset)
        if stop - start < 5:
            continue
        name = f"{channel}_{uuid.uuid4().hex[:10]}.mp4"
        cutter.cut_clip(chunk_path, start, stop, clips_dir / name)
        made.append(store.add_clip(channel, c["start"], c["end"], c["score"], name))
    return made


async def run_session(login: str, *, record=None, live_check=None, notifier=None,
                      chunk_dir=None, use_chat: bool = True, retry_seconds: float = 30,
                      max_chunks: int = None) -> int:
    """Record one live channel chunk by chunk until it goes offline. Returns chunks processed."""
    record = record or (lambda url, out: recorder.record_chunk(url, out))
    chunk_dir = Path(chunk_dir or config.DATA_DIR / "recordings" / login)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    counter = chat_mod.ChatCounter(login)
    chat_task = counter.start() if use_chat else None
    url = watch.stream_url(login)
    index = 0
    try:
        while max_chunks is None or index < max_chunks:
            out = chunk_dir / f"{index:04d}.ts"
            ok = await asyncio.to_thread(record, url, out)
            if ok:
                clips = await asyncio.to_thread(process_chunk, out, index * config.CHUNK_SECONDS,
                                                list(counter.stamps), login)
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


def tick(before: set, running: dict, live: set, start) -> set:
    """One poll: start a session for each channel that just went live; forget finished ones.

    ``start(login)`` must return an asyncio task. Mutates ``running`` and returns ``live``.
    """
    for login in watch.newly_live(before, live):
        running[login] = start(login)
    for login in [k for k, t in running.items() if t.done()]:
        del running[login]
    return live


async def run_watch(poll: float = config.POLL_SECONDS):
    """Poll Twitch for every channel on the watch list and record the ones that go live."""
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        raise RuntimeError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set to watch Twitch")
    running, before = {}, set()
    async with httpx.AsyncClient(timeout=20) as client:
        token = await watch.app_token(client, config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET)

        async def check(login):
            return login in await watch.fetch_live(client, [login], config.TWITCH_CLIENT_ID, token)

        async def notifier(text):
            await notify.send(client, config.DISCORD_WEBHOOK_URL, text)

        def start(login):
            return asyncio.create_task(run_session(login, live_check=check, notifier=notifier))

        while True:
            live = await watch.fetch_live(client, store.channels(), config.TWITCH_CLIENT_ID, token)
            for login in watch.newly_live(before, live):
                if config.DISCORD_WEBHOOK_URL:
                    await notifier(f"{login} is live. Recording started.")
            before = tick(before, running, live, start)
            await asyncio.sleep(poll)
