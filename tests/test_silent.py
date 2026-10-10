import shutil
import subprocess
from unittest.mock import Mock, patch

import numpy as np
import pytest

from app.activity import PROMPT, ActivityAnalyzer
from app.config import ConfigError, load_config
from app.detector import Detection
from app.events import EventManager, EventStore
from app.montage import MontageRecorder
from app.runtime import detector_config, tiered_candidates
from app.utils import Status
from app.zones import ZoneManager


@pytest.fixture
def cfg(tmp_path):
    env = {"CAMERA_URL": "rtsp://e/l", "DATA_DIR": str(tmp_path), "AI_CONFIDENCE": "0.5", "AI_RECORD_CONFIDENCE": "0.1"}
    return load_config(tmp_path / "none", env)


@pytest.fixture
def store(tmp_path):
    result = EventStore(tmp_path)
    yield result
    result.close()


def person(confidence):
    return Detection("person", confidence, (100, 100, 400, 700))


def zone():
    return ZoneManager([]).match(person(1), (720, 1280, 3))


def frame():
    return np.full((720, 1280, 3), 80, np.uint8)


def fake_capture(event, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"mp4")


def test_record_confidence_bounds(tmp_path):
    base = {"CAMERA_URL": "rtsp://e/l", "DATA_DIR": str(tmp_path), "AI_CONFIDENCE": "0.5"}
    assert load_config(tmp_path / "none", base)["ai"]["record_confidence"] == 0  # off by default
    for bad in ("0.5", "0.7", "1"):
        with pytest.raises(ConfigError, match="AI_RECORD_CONFIDENCE"):
            load_config(tmp_path / "none", {**base, "AI_RECORD_CONFIDENCE": bad})


def test_detector_returns_silent_tier_and_candidates_are_split(cfg):
    assert detector_config(cfg["ai"])["confidence"] == 0.1
    assert detector_config(dict(cfg["ai"], record_confidence=0)) is not None
    z = zone()
    recorded, alerting = tiered_candidates([(person(0.3), z), (person(0.62), z), (person(0.2), z)], cfg["ai"])
    assert recorded[("person", "full_frame")][0].confidence == 0.62
    assert alerting[("person", "full_frame")][0].confidence == 0.62
    recorded, alerting = tiered_candidates([(person(0.3), z)], cfg["ai"])
    assert recorded and not alerting


def test_silent_event_keeps_evidence_without_any_notification(cfg, store):
    worker, activity, montage = Mock(), Mock(enabled=True, notifier=Mock()), Mock(enabled=True)
    manager = EventManager(cfg, store, Mock(), worker, Status(cfg), activity, montage)
    event = manager.emit(frame(), person(0.3), zone(), 0, alert=False)
    assert event["notification_status"] == "silent" and event["alert"] is False
    assert event["activity_notification_status"] == "disabled"
    assert (store.root / event["snapshot_path"]).is_file()
    worker.submit.assert_not_called()
    activity.submit.assert_called_once_with(event, analyze=False)
    montage.begin.assert_called_once_with(event)
    # Escalation inside the silent cooldown still alerts; a later silent one is covered by the alert.
    escalated = manager.emit(frame(), person(0.7), zone(), 5, alert=True)
    assert escalated and escalated["notification_status"] == "queued"
    worker.submit.assert_called_once_with(escalated)
    activity.submit.assert_called_with(escalated, analyze=True)
    assert manager.emit(frame(), person(0.3), zone(), 20, alert=False) is None  # alert at 5 covers until 35
    assert manager.emit(frame(), person(0.3), zone(), 40, alert=False) is not None


def test_silent_clip_is_captured_but_not_analyzed(cfg, store, monkeypatch):
    cfg["activity"].update(enabled=True, api_key="k")
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    store.add({"id": "e1", "timestamp": "2026-10-10T09:00:00.000+00:00", "label": "person", "zone": "z"})
    monkeypatch.setattr(analyzer, "_capture", fake_capture)
    analyzer.worker.submit = Mock()
    analyzer._record({"id": "e1", "activity_clip_path": "events/x_activity.mp4", "analyze": False})
    assert store.list_events()[0]["activity_status"] == "captured"
    analyzer.worker.submit.assert_not_called()


def test_montage_notifies_only_if_the_episode_escalated(cfg, store):
    notifier = Mock()
    recorder = MontageRecorder(cfg, store, Status(cfg), notifier)
    recorder.worker.submit = lambda session: recorder._finish(session) or True
    manager = EventManager(cfg, store, Mock(), None, Status(cfg), montage=recorder)
    manager.emit(frame(), person(0.3), zone(), 0, alert=False)
    recorder.feed(frame(), 0, active=True)
    recorder.end()
    assert store.list_events()[0]["montage_notification_status"] == "silent"
    notifier.send_montage.assert_not_called()
    first = manager.emit(frame(), person(0.3), zone(), 100, alert=False)
    recorder.feed(frame(), 100, active=True)
    second = manager.emit(frame(), person(0.8), zone(), 101, alert=True)
    recorder.end()
    notifier.send_montage.assert_called_once()
    saved = {e["id"]: e for e in store.list_events()}
    assert saved[first["id"]]["montage_path"] == saved[second["id"]]["montage_path"]  # both rows link it


def analyzer_with_queue(cfg, store):
    cfg["activity"].update(enabled=True, api_key="k")
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    queued = []
    analyzer.capture_worker.submit = lambda item: queued.append(item) or True
    return analyzer, queued


def test_escalation_inherits_the_recent_silent_clip_only(cfg, store, monkeypatch):
    analyzer, queued = analyzer_with_queue(cfg, store)
    clock = [1000.0]
    monkeypatch.setattr("app.activity.time.monotonic", lambda: clock[0])
    base = {"timestamp": "2026-10-10T09:00:00.000+00:00", "label": "person", "zone": "full_frame"}
    analyzer.submit({**base, "id": "silent"}, analyze=False)
    clock[0] += 20
    analyzer.submit({**base, "id": "alert"}, analyze=True)
    assert queued[1]["prefix_clip_path"] == queued[0]["activity_clip_path"]
    analyzer.submit({**base, "id": "silent2"}, analyze=False)
    clock[0] += 60  # too old: the clips would not be near-continuous any more
    analyzer.submit({**base, "id": "alert2"}, analyze=True)
    assert "prefix_clip_path" not in queued[3]


def test_joined_clip_is_analysed_as_twenty_seconds(cfg, store, monkeypatch):
    analyzer, _ = analyzer_with_queue(cfg, store)
    store.add({"id": "a1", "timestamp": "2026-10-10T09:00:00.000+00:00", "label": "person", "zone": "z"})
    prefix = store.root / "events/2026-10-10/s1_activity.mp4"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.write_bytes(b"mp4")
    monkeypatch.setattr(analyzer, "_capture", fake_capture)
    monkeypatch.setattr(analyzer, "_prepend", Mock(return_value=True))
    analyzer.worker.submit = Mock(return_value=True)
    analyzer._record(
        {
            "id": "a1",
            "activity_clip_path": "events/2026-10-10/a1_activity.mp4",
            "prefix_clip_path": str(prefix.relative_to(store.root)),
        }
    )
    sent = analyzer.worker.submit.call_args.args[0]
    assert sent["clip_seconds"] == 20 and store.list_events()[0]["activity_clip_seconds"] == 20
    with patch("app.activity.requests.post") as post:
        post.side_effect = RuntimeError("stop after building the request")
        try:
            analyzer._request("m", "data:video/mp4;base64,AA==", prompt=PROMPT.replace("10-second", "20-second"))
        except RuntimeError:
            pass
    assert "20-second" in post.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")
def test_prepend_joins_two_clips_without_reencoding(cfg, store, tmp_path):
    analyzer, _ = analyzer_with_queue(cfg, store)
    clips = []
    for name in ("before", "after"):
        clip = tmp_path / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=5:d=10", "-c:v", "libx264"]
            + ["-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", str(clip)],
            check=True,
        )
        clips.append(clip)
    assert analyzer._prepend(clips[0], clips[1])
    duration = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(clips[1])],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert 19 <= float(duration) <= 21
    assert not list(tmp_path.glob("*.joined.mp4")) and not list(tmp_path.glob("*.txt"))
