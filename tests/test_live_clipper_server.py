"""HTTP tests for the live clipper review API and GUI (no ffmpeg needed)."""
import pytest
from fastapi.testclient import TestClient

from live_clipper import config, server, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "")
    return TestClient(server.app)


def test_gui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Waiting for review" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_channels_add_and_reject_bad_login(client):
    assert client.post("/api/channels", json={"login": "Alpha"}).json() == ["alpha"]
    assert client.get("/api/channels").json() == ["alpha"]
    assert client.post("/api/channels", json={"login": "bad login!"}).status_code == 400


def test_clip_file_refuses_path_tricks(client):
    assert client.get("/clips/..%2Fsecret.mp4").status_code in (400, 404)
    assert client.get("/clips/notes.txt").status_code == 400


def test_approve_without_publishing_marks_approved_for_manual_posting(client, tmp_path):
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4")
    r = client.post(f"/api/clips/{clip['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert store.list_clips("pending") == []


def test_approve_takes_the_look_on_screen(client):
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4", zoom_file="a_zoom.mp4", gaming_file="a_gaming.mp4")
    r = client.post(f"/api/clips/{clip['id']}/approve?look=zoom")
    assert r.status_code == 200 and r.json()["approved_file"] == "a_zoom.mp4"


def test_approve_refuses_a_look_the_clip_does_not_have(client):
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4")  # no zoom or gaming version
    assert client.post(f"/api/clips/{clip['id']}/approve?look=gaming").status_code == 400
    assert client.post(f"/api/clips/{clip['id']}/approve?look=nonsense").status_code == 400
    assert len(store.list_clips("pending")) == 1  # a refused approve leaves the clip in review


def test_reject_moves_clip_out_of_review_and_cannot_be_approved(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "a.mp4").write_bytes(b"x")
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4")
    assert client.post(f"/api/clips/{clip['id']}/reject").json() == {"removed": clip["id"]}
    assert not (tmp_path / "clips" / "a.mp4").exists()
    assert client.post(f"/api/clips/{clip['id']}/approve").status_code == 404


def test_unknown_clip_is_404(client, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")
    assert client.post("/api/clips/nope/approve").status_code == 404
    assert client.post("/api/clips/nope/reject").status_code == 404


def test_lan_token_gates_every_request_then_sets_a_cookie(client, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_TOKEN", "s3cret")
    assert client.get("/api/clips").status_code == 403
    assert client.get("/api/clips?key=wrong").status_code == 403
    first = client.get("/api/clips?key=s3cret")
    assert first.status_code == 200 and "lc_key" in first.cookies
    assert client.get("/api/clips").status_code == 200  # the cookie carries it from here on


def test_clip_file_answers_range_requests_with_206(client, tmp_path, monkeypatch):
    clips = tmp_path / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    (clips / "r.mp4").write_bytes(bytes(range(100)))
    part = client.get("/clips/r.mp4", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/100"
    tail = client.get("/clips/r.mp4", headers={"Range": "bytes=-5"})
    assert tail.content == bytes(range(95, 100))
    assert client.get("/clips/r.mp4", headers={"Range": "bytes=500-600"}).status_code == 416
    full = client.get("/clips/r.mp4")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
