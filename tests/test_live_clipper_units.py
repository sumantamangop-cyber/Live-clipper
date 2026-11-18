"""Unit tests for the live clipper: pure logic and mocked HTTP. No ffmpeg, no network."""
import asyncio
import contextlib
import functools
import json

import httpx
import pytest

from live_clipper import chat, config, notify, pipeline, publish, recorder, signals, store, watch, cutter


def _sync(fn):
    """Run an async test body with asyncio.run (no pytest-asyncio dependency)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# --- watch: Helix responses ------------------------------------------------ #
def test_live_logins_keeps_only_live_streams_lowercased():
    payload = {"data": [{"type": "live", "user_login": "Alpha"}, {"type": "", "user_login": "beta"}]}
    assert watch.live_logins(payload) == {"alpha"}


def test_newly_live_is_the_difference():
    assert watch.newly_live({"a"}, {"a", "b"}) == {"b"}
    assert watch.newly_live({"a", "b"}, {"a"}) == set()


@_sync
async def test_fetch_live_sends_headers_and_parses():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["client"] = request.headers["Client-Id"]
        seen["logins"] = request.url.params.get_list("user_login")
        return httpx.Response(200, json={"data": [{"type": "live", "user_login": "alpha"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        live = await watch.fetch_live(client, ["alpha", "beta"], "cid", "tok")
    assert live == {"alpha"}
    assert seen["auth"] == "Bearer tok" and seen["client"] == "cid"
    assert seen["logins"] == ["alpha", "beta"]


@_sync
async def test_fetch_live_with_no_channels_makes_no_call():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: 1 / 0)) as client:
        assert await watch.fetch_live(client, [], "cid", "tok") == set()


# --- signals ---------------------------------------------------------------- #
def test_parse_levels_reads_ametadata_lines():
    text = ("frame:0 pts:0 pts_time:0\nlavfi.astats.Overall.RMS_level=-41.2\n"
            "frame:1 pts:16000 pts_time:1\nlavfi.astats.Overall.RMS_level=-inf\n"
            "frame:2 pts:32000 pts_time:2\nlavfi.astats.Overall.RMS_level=-3.5\n")
    assert signals.parse_levels(text) == [(0.0, -41.2), (2.0, -3.5)]


def test_audio_events_flags_loud_seconds_only():
    levels = [(0, -40.0), (1, -40.0), (2, -41.0), (3, -4.0)]
    events = signals.audio_events(levels, rise_db=10)
    assert [t for t, _ in events] == [3]
    assert events[0][1] > 0


def test_audio_events_empty():
    assert signals.audio_events([]) == []


def test_chat_events_needs_absolute_and_relative_busy():
    quiet = [1, 2, 3]  # 3 messages total: below min_count
    assert signals.chat_events(quiet) == []
    steady = [i * 10 + 1 for i in range(10)]          # one message per 10 s bucket
    burst = [55.0] * 12                               # a 12-message burst in bucket 5
    events = signals.chat_events(steady + burst, bucket=10, min_count=10)
    assert len(events) == 1 and events[0][0] == 55.0
    # Busy in absolute terms but normal for this stream (median baseline is high): no event.
    busy_all_along = [i * 1.0 for i in range(60)] * 1
    assert signals.chat_events(busy_all_along, bucket=10, factor=3, min_count=10) == []


def test_candidates_merge_overlaps_and_rank_by_weight():
    events = [(100.0, 1.0), (110.0, 1.0), (500.0, 0.5)]
    windows = signals.candidates(events, pre=40, post=15, limit=3)
    # 100 and 110 would span 65 s, past the 60 s cap, so they stay separate windows
    assert sorted(w["score"] for w in windows) == [0.5, 1.0, 1.0]
    assert {"start": 460.0, "end": 515.0, "score": 0.5} in windows


def test_is_hot_counts_only_the_last_window():
    stamps = [100.0] * 30                      # 30 messages right now
    assert signals.is_hot(stamps, now=130.0, window=60, min_msgs=30)
    assert not signals.is_hot(stamps, now=200.0, window=60, min_msgs=30)  # they are too old
    assert not signals.is_hot([100.0] * 29, now=130.0, window=60, min_msgs=30)


@_sync
async def test_quiet_chat_records_nothing_and_stops_when_offline(tmp_path):
    calls = {"record": 0}
    checks = iter([True, False])  # live twice, then offline

    def record(url, out):
        calls["record"] += 1
        return True

    async def live_check(login):
        return next(checks)

    processed = await pipeline.run_session(
        "alpha", record=record, live_check=live_check, chunk_dir=tmp_path / "rec",
        use_chat=False, is_hot=lambda s, n: False, poll_seconds=0)
    assert processed == 0 and calls["record"] == 0


@_sync
async def test_recording_goes_on_while_a_chunk_is_being_cut(tmp_path, monkeypatch):
    import threading
    second = threading.Event()  # set when the second chunk starts recording
    seen = {"records": 0, "waited": []}

    def fake_process(path, offset, stamps, channel):  # stands in for the slow transcribe-and-cut
        seen["waited"].append(second.wait(timeout=5))
        return []

    def record(url, out):
        seen["records"] += 1
        if seen["records"] == 2:
            second.set()
        return True

    monkeypatch.setattr(pipeline, "process_chunk", fake_process)
    processed = await pipeline.run_session(
        "alpha", record=record, chunk_dir=tmp_path / "rec", use_chat=False,
        is_hot=lambda s, n: True, poll_seconds=0, max_chunks=2)
    assert processed == 2
    assert seen["waited"][0] is True  # the second chunk recorded before the first was cut


def test_candidates_split_a_busy_stretch_so_scores_match_the_window():
    events = [(100.0, 1.0), (130.0, 1.0), (160.0, 1.0), (190.0, 1.0)]  # one long run of spikes
    windows = signals.candidates(events, pre=40, post=15, limit=10, max_len=60)
    assert all(w["end"] - w["start"] <= 60.0 for w in windows)
    # every event lands in exactly one window, so the scores add back up to the total
    assert round(sum(w["score"] for w in windows), 2) == 4.0


def test_candidates_never_start_before_zero_and_respect_limit():
    windows = signals.candidates([(5.0, 1.0), (300.0, 2.0), (700.0, 3.0)], pre=40, post=15, limit=2)
    assert len(windows) == 2
    assert windows[0]["score"] == 3.0
    assert all(w["start"] >= 0 for w in windows)


# --- recorder / cutter command lines --------------------------------------- #
def test_record_command_chunks_by_duration():
    cmd = recorder.record_command("https://twitch.tv/x", "out.ts", chunk_seconds=600, streamlink=["sl"])
    assert cmd[:1] == ["sl"]
    assert cmd[cmd.index("--hls-duration") + 1] == "600"
    assert cmd[-2:] == ["https://twitch.tv/x", "best"]


def test_audio_levels_command_writes_one_frame_per_second():
    cmd = recorder.audio_levels_command("in.ts", ffmpeg=["ff"])
    af = cmd[cmd.index("-af") + 1]
    assert "asetnsamples=n=16000" in af and "aresample=16000" in af and "levels.txt" in af


def test_cut_command_keeps_the_whole_frame_over_a_blurred_background():
    cmd = cutter.cut_command("src.ts", 12.5, 40.0, "clip.mp4", ffmpeg=["ff"])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "boxblur" in graph and "overlay=(W-w)/2:(H-h)/2" in graph and "crop=ih" not in graph
    assert "scale=1080:-2" in graph and graph.endswith("[v]")
    assert "subtitles=" not in graph
    assert cmd[cmd.index("-ss") + 1] == "12.50"
    assert cmd[cmd.index("-t") + 1] == "27.50"


def test_cut_command_burns_in_subtitles_from_a_bare_file_name():
    cmd = cutter.cut_command("src.ts", 0, 30, "clip.mp4", ffmpeg=["ff"], subs="/tmp/x/clip.ass")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "subtitles=filename=clip.ass[v]" in graph  # styling lives in the ASS file itself
    assert "force_style" not in graph


def test_zoom_look_crops_the_centre_of_a_16_9_frame_to_fill_the_screen():
    cmd = cutter.cut_command("src.ts", 0, 30, "clip.mp4", ffmpeg=["ff"], look="zoom")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:v]crop=trunc(ih*9/32)*2:ih,scale=1080:1920") and graph.endswith("[v]")
    assert "boxblur" not in graph


def test_gaming_look_stacks_the_facecam_over_the_game_and_needs_the_cam_box():
    cmd = cutter.cut_command("src.ts", 0, 30, "clip.mp4", ffmpeg=["ff"], look="gaming", cam=(0, 640, 580, 440),
                             subs="/tmp/x/clip_top.ass")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=580:440:0:640" in graph                      # the facecam box, in source pixels
    assert "vstack=inputs=2" in graph and "pad=1080:776" in graph  # 768 cam + 8 divider on top, 1144 game below
    assert "subtitles=filename=clip_top.ass[v]" in graph
    with pytest.raises(ValueError):
        cutter.cut_command("src.ts", 0, 30, "clip.mp4", ffmpeg=["ff"], look="gaming")


def test_top_captions_sit_under_the_divider_and_bottom_ones_stay_low(tmp_path):
    from live_clipper import captions
    words = [{"w": "hi", "start": 1.0, "end": 1.4}]
    captions.write_ass(words, 0.0, 5.0, tmp_path / "top.ass", top=True)
    captions.write_ass(words, 0.0, 5.0, tmp_path / "bottom.ass")
    top = (tmp_path / "top.ass").read_text(encoding="utf-8")
    bottom = (tmp_path / "bottom.ass").read_text(encoding="utf-8")
    # Style fields end: Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
    assert ",5,2,8,40,40,840,1" in top   # alignment 8 = top centre, 840 px down: under the 776 px divider
    assert ",5,2,2,40,40,420,1" in bottom


def test_ass_groups_words_and_highlights_the_one_being_said(tmp_path):
    from live_clipper import captions
    words = [{"w": w, "start": s, "end": s + 0.4} for w, s in
             [("one", 10.0), ("two", 10.5), ("three", 11.0), ("four", 11.5), ("late", 20.0)]]
    out = tmp_path / "c.ass"
    assert captions.write_ass(words, 10.0, 25.0, out)
    events = [line for line in out.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
    # groups: [one two three] [four] [late]; one event per group, so nothing repeats on screen
    assert len(events) == 3
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:01.40,")  # starts at the first word, relative to t0
    assert "{\\k50}ONE {\\k50}TWO {\\k40}THREE" in events[0]           # karaoke: each word timed to the next word
    assert "FOUR" not in events[0]  # its group only
    assert captions.ass_time(3661.5) == "1:01:01.50"
    assert captions.write_ass(words, 30.0, 40.0, tmp_path / "empty.ass") is False


def test_ass_never_shows_two_captions_at_once(tmp_path):
    from live_clipper import captions
    # the transcriber's word times overlap: "gotta" ends after "play" starts
    words = [{"w": w, "start": s, "end": e} for w, s, e in
             [("wait", 1.0, 1.9), ("gotta", 1.8, 2.6), ("play", 2.5, 2.9), ("on", 2.8, 3.1)]]
    out = tmp_path / "o.ass"
    captions.write_ass(words, 0.0, 5.0, out)
    spans = []
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            spans.append((parts[1], parts[2]))
    assert spans and all(s < e for s, e in spans)
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1 <= s2  # the next caption starts only once the last one is gone


# --- chat ------------------------------------------------------------------- #
def test_handle_line_answers_pings_and_counts_messages():
    assert chat.handle_line("PING :tmi.twitch.tv") == ("pong", "PONG :tmi.twitch.tv")
    assert chat.handle_line(":u!u@u.tmi.twitch.tv PRIVMSG #alpha :hi") == ("msg", None)
    assert chat.handle_line(":tmi.twitch.tv 001 justinfan1 :Welcome") == (None, None)


def test_chat_counter_stores_times_not_text():
    c = chat.ChatCounter("Alpha", t0=100.0)
    c.on_line(":u!u@u PRIVMSG #alpha :secret text", now=107.5)
    assert c.stamps == [7.5]
    assert c.on_line("PING :x", now=108) == "PONG :x"


# --- notify ----------------------------------------------------------------- #
@pytest.mark.parametrize("url,ok", [
    ("https://discord.com/api/webhooks/1/abc", True),
    ("http://discord.com/api/webhooks/1/abc", False),
    ("https://evil.example/api/webhooks/1/abc", False),
    ("https://discord.com/somewhere/else", False),
    ("", False),
])
def test_valid_webhook(url, ok):
    assert notify.valid_webhook(url) is ok


@_sync
async def test_notify_sends_only_to_valid_webhooks_and_truncates():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notify.send(client, "https://discord.com/api/webhooks/1/x", "a" * 5000)
        assert not await notify.send(client, "https://evil.example/api/webhooks/1/x", "hi")
    assert len(sent) == 1 and len(sent[0]["content"]) == 1900


# --- store ------------------------------------------------------------------ #
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_store_clip_lifecycle(data_dir):
    clip = store.add_clip("alpha", 1.0, 30.0, 2.5, "a.mp4")
    assert store.list_clips("pending")[0]["id"] == clip["id"]
    updated = store.set_status(clip["id"], "published", result={"ok": True})
    assert updated["status"] == "published" and updated["result"] == {"ok": True}
    assert store.list_clips("pending") == []
    with pytest.raises(ValueError):
        store.set_status(clip["id"], "bogus")
    with pytest.raises(KeyError):
        store.set_status("missing", "rejected")


def test_store_channels_validate_and_dedupe(data_dir):
    store.add_channel("Alpha")
    assert store.add_channel("alpha") == ["alpha"]
    with pytest.raises(ValueError):
        store.add_channel("not a login!")


# --- publish ---------------------------------------------------------------- #
@_sync
async def test_publish_posts_multipart_with_apikey(tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"fake")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json={"request_id": "r1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await publish.publish(client, "KEY", "profile", str(video), "T" * 150)
    assert out == {"request_id": "r1"}
    assert seen["auth"] == "Apikey KEY"
    assert b'name="video"' in seen["body"] and b"fake" in seen["body"]
    assert b"profile" in seen["body"]


@_sync
async def test_publish_refuses_without_configuration(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: 1 / 0)) as client:
        with pytest.raises(RuntimeError):
            await publish.publish(client, "", "", str(tmp_path / "x.mp4"), "t")


# --- pipeline: the poll state machine ---------------------------------------- #
class _Done:
    def __init__(self, done):
        self._done = done

    def done(self):
        return self._done


def test_tick_starts_new_sessions_and_forgets_finished_ones():
    started = []
    running = {"old": _Done(True), "keep": _Done(False)}
    pipeline.tick(running, {"keep", "new"}, lambda login: started.append(login) or _Done(False))
    assert started == ["new"]
    assert "old" not in running and "keep" in running and "new" in running


def test_tick_skips_switched_off_channels_and_respects_the_cap():
    started = []
    running = {}
    pipeline.tick(running, {"a", "b", "c", "d"}, lambda login: started.append(login) or _Done(False),
                  paused={"b"}, cap=2)
    assert started == ["a", "c"]


def test_tick_stops_a_recording_that_is_switched_off():
    cancelled = []

    class _Task(_Done):
        def cancel(self):
            cancelled.append(True)

    running = {"a": _Task(False)}
    pipeline.tick(running, {"a"}, lambda login: _Done(False), paused={"a"}, cap=2)
    assert cancelled == [True] and "a" not in running


def test_run_watch_refuses_without_twitch_credentials(monkeypatch):
    monkeypatch.setattr(config, "TWITCH_CLIENT_ID", "")
    monkeypatch.setattr(config, "TWITCH_CLIENT_SECRET", "")
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline.run_watch())


def test_run_watch_survives_a_network_error_and_keeps_polling(monkeypatch):
    # a dropped network used to end the watcher for good; it must retry on the next poll
    import httpx
    from live_clipper import watch as watch_mod
    monkeypatch.setattr(config, "TWITCH_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "TWITCH_CLIENT_SECRET", "sec")
    monkeypatch.setattr(pipeline.store, "channels", lambda: ["alpha"])
    calls = []
    polled_again = asyncio.Event()

    async def app_token(*a):
        return "tok"

    async def fetch_live(*a):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("network down")
        polled_again.set()
        return set()

    monkeypatch.setattr(watch_mod, "app_token", app_token)
    monkeypatch.setattr(watch_mod, "fetch_live", fetch_live)

    async def run_briefly():
        watcher = asyncio.create_task(pipeline.run_watch(poll=0.01))
        # wait for the poll after the failure, not a fixed time: a busy machine can be slow to start
        await asyncio.wait_for(polled_again.wait(), timeout=10)
        watcher.cancel()  # the watch loop never returns on its own; cancelling is the normal stop
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    asyncio.run(run_briefly())
    assert len(calls) > 1  # polled again after the failure instead of crashing


# --- buzzwords: Tripwire's rule ------------------------------------------- #
from live_clipper import buzz  # noqa: E402

BUZZ_YML = """\
match_window_s: 3
tiers:
  1: {min_rise_db: 6, weight: 6, phrases: ["clip that"]}
  3: {min_rise_db: 9, weight: 0, phrases: ["rizz"]}
variants:
  "clip that": ["clip dat"]
creators:
  alpha: ["cooked"]
"""


def _words(*pairs):
    return [{"w": w, "start": s, "end": s + 0.4} for w, s in pairs]


def test_buzz_rules_match_variants_and_creator_extras(tmp_path):
    p = tmp_path / "b.yml"
    p.write_text(BUZZ_YML, encoding="utf-8")
    cfg, tiers, rules = buzz.load_rules("Alpha", path=p)
    hits = buzz.find_hits(_words(("Clip", 4.0), ("dat!", 4.4), ("so", 5.0), ("cooked", 6.0)), rules)
    assert {h["phrase"] for h in hits} == {"clip that", "cooked"}
    assert all(h["t"] in (4.0, 6.0) for h in hits)


def test_buzz_fires_only_when_audio_rises_near_the_phrase(tmp_path):
    p = tmp_path / "b.yml"
    p.write_text(BUZZ_YML, encoding="utf-8")
    cfg, tiers, rules = buzz.load_rules("nobody", path=p)
    quiet = [(s, -40.0) for s in range(80)]
    loud = [(s, -40.0 if s != 30 else -20.0) for s in range(80)]  # +20 dB at second 30
    hits = [{"phrase": "clip that", "tier": 1, "t": 29.0}]
    assert buzz.events(hits, buzz.rise_per_second(quiet), cfg, tiers) == []
    (t, score), = buzz.events(hits, buzz.rise_per_second(loud), cfg, tiers)
    assert t == 30.0 and score > 6


def test_buzz_tier_three_needs_a_bigger_rise(tmp_path):
    p = tmp_path / "b.yml"
    p.write_text(BUZZ_YML, encoding="utf-8")
    cfg, tiers, _ = buzz.load_rules("nobody", path=p)
    rise = buzz.rise_per_second([(s, -40.0 if s != 30 else -32.0) for s in range(80)])  # +8 dB
    assert buzz.events([{"phrase": "rizz", "tier": 3, "t": 30.0}], rise, cfg, tiers) == []
