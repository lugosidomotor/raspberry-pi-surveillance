from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import os
import sqlite3
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_config
from app.events import EventManager, EventStore
from app.detector import Detection
from app.runtime import Runtime
from app.storage import StoragePolicy
from app.uploader import RcloneUploader
from app.recorder import ContinuousRecorder
from app.recorder import EventRecorder
import subprocess
import shutil
from app.utils import Status
from app.web import create_app
from app.zones import ZoneManager
from deploy.pi import source


@pytest.fixture
def cfg(tmp_path):
    return load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)})


@pytest.fixture
def store(tmp_path):
    result = EventStore(tmp_path)
    yield result
    result.close()


def emit(cfg, store, stamp, now=0):
    manager = EventManager(cfg, store, Mock(), None, Status(cfg))
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    person = Detection("person", 0.9, (0, 0, 80, 100))
    return manager.emit(image, person, ZoneManager([]).match(person, image.shape), now, stamp)


def test_retention_config_and_validation(tmp_path, cfg):
    assert cfg["storage"]["retention_days"] == 14
    assert (
        load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "RETENTION_DAYS": "7"})["storage"][
            "retention_days"
        ]
        == 7
    )
    for value in ("0", "-1", "1.5", "nan"):
        with pytest.raises(ConfigError):
            load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "RETENTION_DAYS": value})


def test_dst_order_and_legacy_migration(cfg, tmp_path):
    early = {"id": "early", "timestamp": "2026-10-25T02:50:00+02:00", "label": "person", "zone": "full_frame"}
    late = {"id": "late", "timestamp": "2026-10-25T02:10:00+01:00", "label": "person", "zone": "full_frame"}
    db = sqlite3.connect(tmp_path / "events.sqlite3")
    db.execute("CREATE TABLE events(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,payload TEXT NOT NULL)")
    db.execute("INSERT INTO events VALUES(?,?,?)", (early["id"], early["timestamp"], json.dumps(early)))
    db.commit()
    db.close()
    store = EventStore(tmp_path)
    store.add(late)
    assert [e["id"] for e in store.list_events(label="person")] == ["late", "early"]
    store.close()


def test_snapshot_utc_thumbnail_cache(cfg, store):
    event = emit(cfg, store, datetime.fromisoformat("2026-10-25T02:50:00+02:00"))
    assert event["timestamp"].endswith("+00:00")
    assert (store.root / event["thumbnail_path"]).stat().st_size < (store.root / event["snapshot_path"]).stat().st_size
    rt = SimpleNamespace(status=Status(cfg), store=store, start=lambda: None, close=lambda: None)
    with TestClient(create_app(cfg, rt)) as client:
        listed = client.get("/api/events").json()["events"][0]
        assert client.get(listed["thumbnail_url"]).headers["cache-control"] == "private, max-age=86400"
        assert client.get("/api/status").headers["cache-control"] == "no-store"
        assert client.get("/api/status").json()["retention_days"] == 14


def test_retention_consistent_and_protects_active(cfg, store):
    old = emit(cfg, store, datetime.fromisoformat("2026-01-01T00:00:00+00:00"))
    recent = emit(cfg, store, datetime.now().astimezone())
    active = store.root / "recordings" / "active.mp4.tmp"
    active.parent.mkdir()
    active.write_bytes(b"active")
    os.utime(active, (0, 0))
    policy = StoragePolicy(cfg["storage"], store, Status(cfg))
    assert store.claim_media(old["snapshot_path"])
    policy.sweep(time.time())
    assert (store.root / old["snapshot_path"]).exists()
    store.upload_result(old["snapshot_path"], True)
    policy.sweep(time.time())
    assert [e["id"] for e in store.list_events()] == [recent["id"]]
    assert not (store.root / old["thumbnail_path"]).exists()
    assert active.exists()
    assert len(store.pending_media(time.time())) == 1


def test_low_disk_pauses_then_recovers(cfg, store):
    status = Status(cfg)
    policy = StoragePolicy(cfg["storage"], store, status)
    with patch("app.storage.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
        assert not policy.poll()
    assert status.snapshot()["storage_low"]
    policy.next_check = 0
    with patch("app.storage.shutil.disk_usage", return_value=SimpleNamespace(free=10**12)):
        assert policy.poll()


def test_missing_upload_is_terminal(cfg, store):
    event = emit(cfg, store, datetime.now().astimezone())
    entry = store.pending_media(time.time())[0]
    (store.root / event["snapshot_path"]).unlink()
    uploader = RcloneUploader(cfg["backup"], store, Status(cfg))
    assert not uploader.upload(entry)
    assert not store.pending_media(1e12)


def camera_pipe():
    read_end, write_end = os.pipe()
    return os.fdopen(read_end, "rb", buffering=0), write_end


def test_source_camera_keeps_docker_stdin(tmp_path):
    args = SimpleNamespace(rtsp_url="rtsp://127.0.0.1:8554/cam", status_file=tmp_path / "source.json")
    stdout, write_end = camera_pipe()
    capture, publisher = Mock(stdout=stdout), Mock()
    capture.poll.return_value = None
    publisher.poll.return_value = 0  # the publisher exits: only it is replaced, then we stop
    with (
        patch.object(source.subprocess, "Popen", side_effect=[capture, publisher]) as popen,
        patch.object(source.subprocess, "run"),
        patch.object(source, "stop_process") as stopped,
        patch.object(source.STOP, "wait", return_value=False),
        patch.object(source.STOP, "is_set", side_effect=[False, True]),
        patch.object(source, "read_env", return_value={}),  # independent of the developer's .env
    ):
        source.publish(args, "rpicam-vid")
    os.close(write_end)
    command = popen.call_args_list[1].args[0]
    assert command[:2] == ["docker", "compose"]
    assert "--interactive" in command and "-T" in command
    assert command[command.index("publisher") + 1] == "ffmpeg"
    assert "--no-deps" in command
    assert popen.call_args_list[1].kwargs["stdin"] == source.subprocess.PIPE  # fed by the relay, not the camera
    assert command[command.index("pipe:0") - 1] == "-i"
    capture_command = popen.call_args_list[0].args[0]
    assert capture_command[capture_command.index("--bitrate") + 1] == "2000000"
    # Default: 1280x720 with libcamera's own sensor mode (the full-sensor mode is opt-in).
    assert "--mode" not in capture_command
    assert capture_command[capture_command.index("--width") + 1] == "1280"
    assert capture_command[capture_command.index("--height") + 1] == "720"
    # Capture rate and the FFmpeg input rate must agree, or timestamps drift.
    assert capture_command[capture_command.index("--framerate") + 1] == command[command.index("-r") + 1] == "12"
    assert capture in [call.args[0] for call in stopped.call_args_list]


def test_relay_never_blocks_the_camera_and_feeds_new_publishers_live():
    stdout, write_end = camera_pipe()
    relay = source.Relay(stdout, chunk=4, depth=2)
    relay.start()
    for _ in range(8):
        os.write(write_end, b"abcd")
        time.sleep(0.02)
    assert relay.dropped >= 1  # no publisher: data is dropped, the camera pipe keeps draining
    received = []
    sink = SimpleNamespace(write=received.append, flush=lambda: None)
    relay.attach(sink)  # stale chunks are discarded
    os.write(write_end, b"live")
    deadline = time.monotonic() + 2
    while not received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert received == [b"live"]
    os.close(write_end)
    assert relay.ended.wait(2)


def test_silent_camera_restarts_the_capture(tmp_path):
    args = SimpleNamespace(rtsp_url="rtsp://127.0.0.1:8554/cam", status_file=tmp_path / "source.json")
    stdout, write_end = camera_pipe()
    capture, publisher = Mock(stdout=stdout), Mock()
    capture.poll.return_value = publisher.poll.return_value = None
    with (
        patch.object(source.subprocess, "Popen", side_effect=[capture, publisher]),
        patch.object(source.subprocess, "run"),
        patch.object(source, "stop_process"),
        patch.object(source.STOP, "wait", return_value=False),
        patch.object(source, "STALL_SECONDS", -1),
        pytest.raises(RuntimeError, match="stalled"),
    ):
        source.publish(args, "rpicam-vid")
    os.close(write_end)


def test_pipeline_retry_and_stale_frames(cfg):
    runtime = Runtime(cfg)
    runtime._pipeline = Mock(side_effect=[ValueError("model missing"), None])
    with patch.object(runtime.stop, "wait", return_value=False) as wait:
        runtime._run()
    assert runtime._pipeline.call_count == 2
    wait.assert_called_once_with(1)
    runtime.camera = SimpleNamespace(connected=True, generation=1)
    assert runtime.packet_is_current(SimpleNamespace(generation=1, captured_at=time.monotonic()))
    assert not runtime.packet_is_current(SimpleNamespace(generation=0, captured_at=time.monotonic()))
    assert not runtime.packet_is_current(SimpleNamespace(generation=1, captured_at=0))
    runtime.store.close()


def test_manifest_incremental_and_partial_lines(cfg, store):
    recorder = ContinuousRecorder(cfg, store)
    folder = store.root / "recordings" / "2026-10-03"
    folder.mkdir(parents=True)
    first, second = folder / "a.mp4.tmp", folder / "b.mp4.tmp"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    recorder.list_path = folder / "manifest.csv"
    recorder.list_path.write_text(f'"{first}",0,1\n"{second}",1,2')
    recorder._collect_closed()
    assert first.with_suffix("").exists() and second.exists()
    with recorder.list_path.open("a") as handle:
        handle.write("\n")
    recorder._collect_closed()
    assert second.with_suffix("").exists()
    assert recorder.manifest_offset == recorder.list_path.stat().st_size


def test_real_ffmpeg_pre_roll_concat(cfg, store, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    sample = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x120:r=10",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-g",
            "10",
            str(sample),
        ],
        check=True,
        timeout=15,
    )
    event = emit(cfg, store, datetime.now().astimezone())
    recorder = EventRecorder(cfg, store, Status(cfg))
    recorder.path = store.root / "events" / "clip.mp4"
    recorder.temporary = recorder.path.with_suffix(".mp4.tmp")
    shutil.copyfile(sample, recorder.temporary)
    # The real event and segment muxers can choose different stream time bases.
    different = tmp_path / "different.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(sample),
            "-c",
            "copy",
            "-video_track_timescale",
            "90000",
            str(different),
        ],
        check=True,
        timeout=15,
    )
    shutil.copyfile(different, recorder.temporary)
    recorder.parts_dir = tmp_path / "parts"
    recorder.parts_dir.mkdir()
    part = recorder.parts_dir / "0.part"
    part.hardlink_to(sample)
    recorder.pre_parts = [part]
    recorder.stderr = __import__("tempfile").TemporaryFile()
    recorder.event_ids = [event["id"]]
    recorder._finish(0)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(recorder.path)],
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert 4 <= float(probe.stdout) < 4.2
    assert not (tmp_path / "parts").exists()
    assert store.list_events()[0]["recording_status"] == "completed"


def test_continuous_poll_throttled(cfg, store):
    cfg["recording"]["continuous"] = True
    recorder = ContinuousRecorder(cfg, store)
    with (
        patch.object(recorder, "_prepare_days") as prepare,
        patch.object(recorder, "_collect_closed") as collect,
        patch.object(recorder, "_start"),
    ):
        recorder.poll()
        recorder.poll()
    assert prepare.call_count == collect.call_count == 1


def test_upload_diagnostic_never_echoes_credentials(cfg, store, caplog):
    emit(cfg, store, datetime.now().astimezone())
    uploader = RcloneUploader(dict(cfg["backup"], remote="storage:", path="archive"), store, Status(cfg))

    def fake_process(*args, **kwargs):
        kwargs["stderr"].write(b"secret-password: permission denied")
        return SimpleNamespace(wait=lambda timeout: 1, poll=lambda: 1)

    with patch("app.uploader.subprocess.Popen", side_effect=fake_process):
        assert not uploader.upload(store.pending_media(time.time())[0])
    assert "permission denied" in caplog.text
    assert "secret-password" not in caplog.text
