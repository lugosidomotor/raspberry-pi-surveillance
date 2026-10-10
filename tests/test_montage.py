from email.header import decode_header, make_header
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_config
from app.detector import Detection
from app.events import EventManager, EventStore
from app.montage import HEADER_HEIGHT, MontageRecorder, compose
from app.notifier import NtfyNotifier
from app.storage import StoragePolicy
from app.utils import Status
from app.web import create_app
from app.zones import ZoneManager


@pytest.fixture
def cfg(tmp_path):
    return load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)})


@pytest.fixture
def store(tmp_path):
    result = EventStore(tmp_path)
    yield result
    result.close()


def frame(value=90):
    return np.full((720, 1280, 3), value, dtype=np.uint8)


def person_event(cfg, store, montage, now=0):
    manager = EventManager(cfg, store, Mock(), None, Status(cfg), montage=montage)
    detection = Detection("person", 0.9, (100, 100, 400, 700))
    zone = ZoneManager([]).match(detection, (720, 1280, 3))
    return manager, manager.emit(frame(), detection, zone, now)


def synchronous(recorder):
    """Run the worker's job inline so tests see the saved montage immediately."""
    recorder.worker.submit = lambda session: recorder._finish(session) or True
    return recorder


def test_episode_frames_until_motion_ends_then_saved_and_sent(cfg, store):
    notifier = Mock()
    recorder = synchronous(MontageRecorder(cfg, store, Status(cfg), notifier))
    _, event = person_event(cfg, store, recorder)
    assert event["montage_status"] == "recording"
    for second in range(5):
        recorder.feed(frame(40 * second), 100 + second, active=True)
        recorder.feed(frame(), 100 + second + 0.5, active=True)  # within the 1 s interval: skipped
    recorder.feed(frame(), 106, active=False)
    assert recorder.session is not None  # quiet for 2 s only: the episode stays open
    recorder.feed(frame(), 104.5 + cfg["montage"]["idle_seconds"], active=False)  # last motion was at 104.5
    saved = store.list_events()[0]
    assert saved["montage_status"] == "completed" and saved["montage_frames"] == 5
    image = cv2.imread(str(store.root / saved["montage_path"]))
    assert image.shape == (HEADER_HEIGHT + 2 * 270, 4 * 480, 3)
    assert ("montage" in store.root.joinpath(saved["montage_path"]).name) and recorder.session is None
    notifier.send_montage.assert_called_once()
    assert notifier.send_montage.call_args.args[2] == 5
    assert saved["montage_path"] in {m["path"] for m in store.pending_media(1e12)}  # enters the backup catalog


def test_return_within_idle_window_continues_the_same_montage(cfg, store):
    notifier = Mock()
    recorder = synchronous(MontageRecorder(cfg, store, Status(cfg), notifier))
    person_event(cfg, store, recorder)
    for tick in (0, 1, 2):
        recorder.feed(frame(), tick, active=True)
    for tick in range(3, 50):
        recorder.feed(frame(), tick, active=False)  # 47 s of quiet: no empty frames are added
    assert len(recorder.session["tiles"]) == 3
    for tick in (50, 51):
        recorder.feed(frame(), tick, active=True)  # back within a minute: same montage
    recorder.feed(frame(), 51 + cfg["montage"]["idle_seconds"], active=False)
    assert recorder.session is None
    notifier.send_montage.assert_called_once()
    assert notifier.send_montage.call_args.args[2] == 5


def test_long_episode_stays_bounded_and_evenly_spread(cfg, store):
    cfg["montage"]["max_frames"] = 8
    recorder = MontageRecorder(cfg, store, Status(cfg))
    _, event = person_event(cfg, store, recorder)
    for tick in range(0, 200):
        recorder.feed(frame(), float(tick), active=True)
    session = recorder.session
    assert 4 <= len(session["tiles"]) <= 8
    assert session["interval"] >= 16  # doubled whenever the buffer overflowed
    stamps = [stamp for stamp, _ in session["tiles"]]
    assert stamps == sorted(stamps)


def test_later_events_join_open_episode_and_max_duration_closes_it(cfg, store):
    recorder = synchronous(MontageRecorder(cfg, store, Status(cfg)))
    manager, first = person_event(cfg, store, recorder)
    recorder.feed(frame(), 0, active=True)
    detection = Detection("person", 0.8, (10, 10, 300, 700))
    second = manager.emit(frame(), detection, ZoneManager([]).match(detection, (720, 1280, 3)), 60)
    assert {e["id"]: e["montage_status"] for e in store.list_events()}[second["id"]] == "joined"
    recorder.feed(frame(), recorder.session["started"] + cfg["montage"]["max_seconds"], active=True)
    assert recorder.session is None
    assert {e["id"]: e["montage_status"] for e in store.list_events()}[first["id"]] == "completed"


def test_disconnect_without_frames_marks_failure_and_disabled_is_inert(cfg, store):
    recorder = MontageRecorder(cfg, store, Status(cfg))
    _, event = person_event(cfg, store, recorder)
    recorder.end()
    assert store.list_events()[0]["montage_status"] == "failed"
    cfg["montage"]["enabled"] = False
    disabled = MontageRecorder(cfg, store, Status(cfg))
    _, event = person_event(cfg, store, disabled, now=100)
    assert event["montage_status"] == "disabled" and disabled.session is None
    disabled.feed(frame(), 1, active=True)
    assert disabled.close(1)


def test_compose_grid_scales_mismatched_tiles():
    stamp = datetime(2026, 10, 8, 21, 0, 0)
    tiles = [(stamp, np.zeros((270, 480, 3), np.uint8))] * 2 + [(stamp, np.zeros((100, 100, 3), np.uint8))]
    assert compose(tiles, "person").shape == (HEADER_HEIGHT + 270, 3 * 480, 3)


def test_montage_notification_attaches_image_with_encoded_title(cfg, store, tmp_path):
    path = tmp_path / "event_montage.jpg"
    path.write_bytes(b"jpeg")
    store.add({"id": "e1", "timestamp": "2026-10-08T19:00:00.000+00:00", "label": "person", "zone": "kapu"})
    notifier = NtfyNotifier(dict(cfg["notifications"], url="https://ntfy.sh", topic="test"), store, Status(cfg))
    event = {"id": "e1", "label": "person", "zone": "kapu", "is_test": False}
    start, end = datetime(2026, 10, 8, 21, 0, 0), datetime(2026, 10, 8, 21, 0, 42)
    with patch("app.notifier.requests.Session.post") as post:
        assert notifier.send_montage(event, path, 12, start, end) == "sent"
    headers = post.call_args.kwargs["headers"]
    for value in headers.values():
        value.encode("latin-1")
    assert str(make_header(decode_header(headers["Title"]))) == "🎞️ Mozgás vége · 12 kép"
    assert str(make_header(decode_header(headers["Message"]))) == "person · kapu · 21:00:00–21:00:42"
    assert headers["X-Filename"] == "event_montage.jpg"
    assert store.list_events()[0]["montage_notification_status"] == "sent"


def test_montage_config_env_and_validation(tmp_path):
    base = {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)}
    cfg = load_config(tmp_path / "none", {**base, "MONTAGE_ENABLED": "false", "MONTAGE_MAX_FRAMES": "12"})
    assert cfg["montage"]["enabled"] is False and cfg["montage"]["max_frames"] == 12
    assert cfg["montage"]["idle_seconds"] == 60
    assert load_config(tmp_path / "none", {**base, "MONTAGE_IDLE_SECONDS": "30"})["montage"]["idle_seconds"] == 30
    with pytest.raises(ConfigError):
        load_config(tmp_path / "none", {**base, "MONTAGE_IDLE_SECONDS": "0"})
    with pytest.raises(ConfigError, match="MONTAGE_MAX_FRAMES"):
        load_config(tmp_path / "none", {**base, "MONTAGE_MAX_FRAMES": "1"})
    with pytest.raises(ConfigError):
        load_config(tmp_path / "none", {**base, "MONTAGE_INTERVAL_SECONDS": "0"})


def test_completed_montage_is_served_and_expires_with_its_event(cfg, store):
    recorder = synchronous(MontageRecorder(cfg, store, Status(cfg)))
    _, event = person_event(cfg, store, recorder)
    recorder.feed(frame(), 0, active=True)
    recorder.end()
    saved = store.list_events()[0]
    rt = SimpleNamespace(status=Status(cfg), store=store, start=lambda: None, close=lambda: None)
    with TestClient(create_app(cfg, rt)) as client:
        url = client.get("/api/events").json()["events"][0]["montage_url"]
        assert url.endswith("_montage.jpg") and client.get(url).status_code == 200
    StoragePolicy(cfg["storage"], store, Status(cfg)).sweep(10**12)
    assert not (store.root / saved["montage_path"]).exists()
