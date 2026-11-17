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


def test_approve_is_503_until_publishing_is_configured(client, tmp_path):
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4")
    r = client.post(f"/api/clips/{clip['id']}/approve")
    assert r.status_code == 503
    assert store.list_clips("pending")[0]["id"] == clip["id"]  # nothing changed


def test_reject_moves_clip_out_of_review_and_cannot_be_approved(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")
    clip = store.add_clip("alpha", 0, 30, 1.0, "a.mp4")
    assert client.post(f"/api/clips/{clip['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/clips/{clip['id']}/approve").status_code == 409


def test_unknown_clip_is_404(client, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "KEY")
    monkeypatch.setattr(config, "UPLOAD_POST_PROFILE", "creator")
    assert client.post("/api/clips/nope/approve").status_code == 404
    assert client.post("/api/clips/nope/reject").status_code == 404
