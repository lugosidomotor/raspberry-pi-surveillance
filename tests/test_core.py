import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_config
from app.detector import Detection
from app.events import EventManager, EventStore
from app.motion import MotionDetector, padded_crop
from app.notifier import NtfyNotifier
from app.recorder import ContinuousRecorder
from app.uploader import RcloneUploader
from app.utils import Status
from app.validator import TemporalValidator
from app.web import create_app
from app.zones import ZoneManager


@pytest.fixture
def cfg(tmp_path):
    return load_config(tmp_path / "missing.yaml", {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)})


@pytest.fixture
def store(tmp_path):
    catalog = EventStore(tmp_path)
    yield catalog
    catalog.close()


def test_missing_camera_is_clear_even_if_yaml_contains_url(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("camera:\n  url: rtsp://example/live\n")
    with pytest.raises(ConfigError, match="CAMERA_URL is required"):
        load_config(path, {})


def test_missing_optional_env_disables_integrations(cfg):
    assert cfg["ai"]["tracked_objects"] == ["person"]
    assert not cfg["notifications"]["enabled"]
    assert not cfg["backup"]["enabled"]
    assert not cfg["recording"]["events"]
    assert not cfg["recording"]["continuous"]
    assert cfg["web"]["enabled"]
    assert not cfg["web"]["live_stream_url"]


@pytest.mark.parametrize(
    "optional",
    [
        {"NTFY_URL": "https://ntfy.sh"},
        {"NTFY_TOPIC": "test"},
        {"BACKUP_ENABLED": "true"},
        {"BACKUP_ENABLED": "true", "BACKUP_REMOTE": "storage:"},
    ],
)
def test_partial_optional_config_does_not_fail(tmp_path, optional):
    config = load_config(tmp_path / "missing", {"CAMERA_URL": "rtsp://example/live", **optional})
    assert not config["notifications"]["enabled"]
    assert not config["backup"]["enabled"]


def test_defaults_yaml_env_precedence(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("ai:\n  confidence: 0.8\n  max_fps: 2\nrecording:\n  events: true\n")
    cfg = load_config(config, {"CAMERA_URL": "rtsp://example/live", "AI_CONFIDENCE": "0.9"})
    assert cfg["ai"]["confidence"] == 0.9
    assert cfg["ai"]["max_fps"] == 2
    assert not cfg["recording"]["events"]  # optional switch is environment-only


@pytest.mark.parametrize(
    "env",
    [
        {"AI_MAX_FPS": "0"},
        {"AI_IMAGE_SIZE": "321"},
        {"RECORD_EVENTS": "maybe"},
        {"AI_VALIDATION_WINDOW": "1"},
        {"MOTION_REQUIRED_FRAMES": "NaN"},
        {"MOTION_MAX_FPS": "0"},
        {"MOTION_MAX_FPS": "-1"},
        {"MOTION_MAX_FPS": "nan"},
        {"MOTION_MAX_FPS": "inf"},
    ],
)
def test_invalid_settings_fail(tmp_path, env):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing", {"CAMERA_URL": "rtsp://example/live", **env})


def test_validation_negative_frames_gap_and_zone_separation(cfg):
    validator = TemporalValidator(cfg["validation"])
    person = {("person", "entrance"): "person"}
    assert not validator.update(person, 0)
    assert not validator.update({}, 0.4)
    assert validator.update(person, 0.8) == person
    assert not validator.update({}, 1.2)  # no stale event
    assert not validator.update({("person", "driveway"): "person"}, 1.6)
    assert not validator.update(person, 10)  # old votes expire


def test_zone_match_ignore_and_outside():
    zones = ZoneManager(
        [
            {"name": "sidewalk", "rectangle": [0, 0, 1, 0.2], "rules": {"person": "ignore"}},
            {"name": "entrance", "rectangle": [0, 0.2, 0.5, 1], "rules": {"person": "event"}},
        ]
    )
    assert zones.match(Detection("person", 0.9, (1, 1, 10, 10)), (100, 100, 3)) is None
    assert zones.match(Detection("person", 0.9, (10, 10, 30, 90)), (100, 100, 3)).name == "entrance"
    assert zones.match(Detection("person", 0.9, (60, 10, 90, 90)), (100, 100, 3)) is None


def test_motion_and_crop(cfg):
    settings = dict(cfg["motion"], resize_width=100, min_area=50, warmup_frames=3, required_frames=2, learning_rate=0)
    motion = MotionDetector(settings)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    for _ in range(5):
        assert not motion.process(frame).confirmed
    moving = frame.copy()
    moving[20:80, 20:70] = 255
    assert not motion.process(moving).confirmed
    assert motion.process(moving).confirmed
    crop, offset = padded_crop(frame, [(20, 20, 40, 60)], 0.25)
    assert offset == (15, 10) and crop.shape[:2] == (60, 30)


def test_motion_sampling_rate_reset_and_no_catchup(cfg):
    motion = MotionDetector(cfg["motion"])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    with patch.object(motion, "process", return_value=SimpleNamespace(confirmed=False, boxes=[])) as process:
        for index in range(24):
            motion.sample(frame, index / 24)
        assert process.call_count == 8
        assert motion.sample(frame, 10) is not None
        assert motion.sample(frame, 10.01) is None
        motion.reset()
        assert motion.sample(frame, 10.02) is not None
        assert process.call_count == 10


def test_motion_sampling_only_counts_analyzed_frames(cfg):
    settings = dict(cfg["motion"], resize_width=100, min_area=50, warmup_frames=0, required_frames=2)
    motion = MotionDetector(settings)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    motion.sample(frame, 0)
    assert motion.frames == 1
    assert motion.sample(frame, 0.01) is None
    assert motion.frames == 1
    motion.sample(frame, 0.125)
    assert motion.frames == 2


def test_motion_fps_config_precedence(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("motion:\n  max_fps: 5\n")
    env = {"CAMERA_URL": "rtsp://example/live"}
    assert load_config(tmp_path / "missing", env)["motion"]["max_fps"] == 8
    assert load_config(config, env)["motion"]["max_fps"] == 5
    assert load_config(config, dict(env, MOTION_MAX_FPS="6"))["motion"]["max_fps"] == 6


def new_event(cfg, store, worker=None):
    manager = EventManager(cfg, store, Mock(), worker, Status(cfg))
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detection = Detection("person", 0.91, (20, 10, 80, 100))
    zone = ZoneManager([]).match(detection, frame.shape)
    event = manager.emit(frame, detection, zone, 0)
    return manager, frame, detection, zone, event


def test_event_snapshot_metadata_cooldown_and_restart(cfg, store):
    manager, frame, detection, zone, event = new_event(cfg, store)
    assert event["confidence"] == 0.91 and event["zone"] == "full_frame"
    assert cv2.imread(str(store.root / event["snapshot_path"])) is not None
    assert not list(store.root.rglob("*.tmp"))
    assert manager.emit(frame, detection, zone, 1) is None
    assert store.list_events()[0]["id"] == event["id"]
    another = EventStore(store.root)
    assert another.list_events()[0]["id"] == event["id"]
    another.close()


def test_ntfy_attachment_auth_and_cooldown(cfg, store):
    _, _, _, _, event = new_event(cfg, store)
    settings = dict(cfg["notifications"], url="https://ntfy.sh", topic="test", token="secret")
    notifier = NtfyNotifier(settings, store, Status(cfg))
    with patch("app.notifier.requests.Session.post") as post:
        assert notifier.send(event) == "sent"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
        assert post.call_args.kwargs["headers"]["X-Filename"].endswith(".jpg")
        assert notifier.send(event) == "cooldown"
        post.assert_called_once()


def test_failed_notification_is_persisted(cfg, store):
    import requests

    _, _, _, _, event = new_event(cfg, store)
    notifier = NtfyNotifier(dict(cfg["notifications"], url="https://ntfy.sh", topic="test"), store, Status(cfg))
    with patch("app.notifier.requests.Session.post", side_effect=requests.ConnectionError):
        assert notifier.send(event) == "failed"
    assert store.list_events()[0]["notification_status"] == "failed"


def test_test_event_and_notification_are_marked(cfg, store):
    cfg["camera"]["test_stream"] = True
    _, _, _, _, event = new_event(cfg, store)
    assert event["is_test"] is True
    assert store.list_events()[0]["is_test"] is True
    notifier = NtfyNotifier(dict(cfg["notifications"], url="https://ntfy.sh", topic="test"), store, Status(cfg))
    with patch("app.notifier.requests.Session.post") as post:
        assert notifier.send(event) == "sent"
        assert post.call_args.kwargs["headers"]["Title"].startswith("TEST STREAM")


@pytest.mark.parametrize("verdict,icon,priority", [("Gyanús", "🛑", "high"), ("Nem gyanús", "✅", "default")])
def test_activity_notification_title_starts_with_verdict_emoji(cfg, store, verdict, icon, priority):
    from email.header import decode_header, make_header

    _, _, _, _, event = new_event(cfg, store)
    notifier = NtfyNotifier(dict(cfg["notifications"], url="https://ntfy.sh", topic="test"), store, Status(cfg))
    activity = {**event, "activity_verdict": verdict, "activity_summary": f"{verdict} — Egy személy áll."}
    with patch("app.notifier.requests.Session.post") as post:
        assert notifier.send_activity(activity) == "sent"
    headers = post.call_args.kwargs["headers"]
    # Headers must be Latin-1 encodable for http.client; ntfy decodes the RFC 2047 title.
    headers["Title"].encode("latin-1")
    assert str(make_header(decode_header(headers["Title"]))) == f"{icon} {verdict} · kamerafigyelés"
    assert headers["Priority"] == priority


def test_uploader_never_queues_active_files_and_deletes_only_after_success(cfg, store):
    folder = store.root / "recordings" / "2026-10-03"
    folder.mkdir(parents=True)
    active = folder / "active.mp4.tmp"
    active.write_bytes(b"active")
    final = folder / "closed.mp4"
    final.write_bytes(b"closed")
    settings = dict(cfg["backup"], remote="storage:", path="camera", delete_after_upload=True)
    uploader = RcloneUploader(settings, store, Status(cfg))
    uploader.scan()
    pending = store.pending_media(1e12)
    assert len(pending) == 1 and pending[0]["path"].endswith("closed.mp4")
    failed = SimpleNamespace(wait=lambda timeout: 1, poll=lambda: 1)
    with patch("app.uploader.subprocess.Popen", return_value=failed):
        assert not uploader.upload(pending[0])
    assert final.exists() and active.exists()
    completed = SimpleNamespace(wait=lambda timeout: 0, poll=lambda: 0)
    with patch("app.uploader.subprocess.Popen", return_value=completed):
        assert uploader.upload(pending[0])
    assert not final.exists() and active.exists()


def test_continuous_only_finalizes_manifest_closed_segments(cfg, store):
    recorder = ContinuousRecorder(cfg, store)
    directory = store.root / "recordings" / "2026-10-03"
    directory.mkdir(parents=True)
    closed = directory / "closed.mp4.tmp"
    closed.write_bytes(b"closed")
    active = directory / "active.mp4.tmp"
    active.write_bytes(b"active")
    recorder.list_path = directory / "segments.csv"
    recorder.list_path.write_text(f'"{closed}",0,300\n')
    recorder._collect_closed()
    assert closed.with_suffix("").exists() and not closed.exists()
    assert active.exists()
    assert len(store.pending_media(1e12)) == 1


def test_web_starts_without_optional_env_and_health_offline_is_ok(cfg, store):
    rt = SimpleNamespace(status=Status(cfg), store=store, start=lambda: None, close=lambda: None)
    rt.status.update(camera="disconnected", pipeline="running")
    with TestClient(create_app(cfg, rt)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/events").status_code == 200
        assert client.get("/tablet").status_code == 200  # the always-on wall/tablet view
        assert client.get("/health").json()["status"] == "ok"
        status = client.get("/api/status").json()
        assert not status["notifications_enabled"] and not status["backup_enabled"]
        assert "example/live" not in json.dumps(status)
        assert client.get("/api/events?limit=1000").status_code == 422
        assert client.get("/media/logs/camera.log").status_code == 404
        assert client.get("/media/%2e%2e/events.sqlite3").status_code == 404


def test_web_event_media_and_ai_failure_health(cfg, store):
    _, _, _, _, event = new_event(cfg, store)
    rt = SimpleNamespace(status=Status(cfg), store=store, start=lambda: None, close=lambda: None)
    with TestClient(create_app(cfg, rt)) as client:
        listed = client.get("/api/events").json()["events"][0]
        assert client.get(listed["snapshot_url"]).status_code == 200
        assert client.get("/api/events?label=car").json()["events"] == []
        rt.status.update(pipeline="failed")
        assert client.get("/health").status_code == 503


def test_web_disabled_preserves_health_and_api(cfg, store):
    cfg["web"]["enabled"] = False
    rt = SimpleNamespace(status=Status(cfg), store=store, start=lambda: None, close=lambda: None)
    with TestClient(create_app(cfg, rt)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200


def test_instant_confidence_skips_second_frame_only_when_enabled(cfg):
    cfg["validation"]["instant_confidence"] = 0.8
    validator = TemporalValidator(cfg["validation"])
    zone = "full_frame"  # the validator never inspects the zone object
    sure = {("person", "full_frame"): (Detection("person", 0.86, (0, 0, 10, 10)), zone)}
    unsure = {("person", "full_frame"): (Detection("person", 0.6, (0, 0, 10, 10)), zone)}
    assert validator.update(sure, 0) == sure
    validator.reset()
    assert not validator.update(unsure, 0)  # still needs a second positive frame
    assert validator.update(unsure, 0.5) == unsure
    cfg["validation"]["instant_confidence"] = 0
    assert not TemporalValidator(cfg["validation"]).update(sure, 0)


def test_instant_confidence_config_bounds(tmp_path):
    base = {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path), "AI_CONFIDENCE": "0.5"}
    cfg = load_config(tmp_path / "none", {**base, "AI_VALIDATION_INSTANT_CONFIDENCE": "0.8"})
    assert cfg["validation"]["instant_confidence"] == 0.8
    for bad in ("0.4", "1.5"):
        with pytest.raises(ConfigError, match="INSTANT_CONFIDENCE"):
            load_config(tmp_path / "none", {**base, "AI_VALIDATION_INSTANT_CONFIDENCE": bad})


def test_instant_alert_uses_small_image_and_one_session_per_thread(cfg, store):
    _, _, _, _, event = new_event(cfg, store)
    snapshot = store.root / event["snapshot_path"]
    cv2.imwrite(str(snapshot), np.full((720, 1280, 3), 120, np.uint8))
    notifier = NtfyNotifier(
        dict(cfg["notifications"], url="https://ntfy.sh", topic="t", cooldown_seconds=0), store, Status(cfg)
    )
    with patch("app.notifier.requests.Session") as session_class:
        assert notifier.send(event) == "sent" and notifier.send(event) == "sent"
    session_class.assert_called_once()  # keep-alive connection reused
    data = session_class.return_value.post.call_args.kwargs["data"]
    sent = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert sent.shape[1] == 640 and len(data) < snapshot.stat().st_size
