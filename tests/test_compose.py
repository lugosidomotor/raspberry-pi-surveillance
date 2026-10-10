from pathlib import Path
from unittest.mock import Mock, patch
import json

import pytest
import yaml

from app import publisher

ROOT = Path(__file__).resolve().parents[1]


def test_local_stack_has_shared_services_and_no_second_project():
    stack = yaml.safe_load((ROOT / "docker-compose.stream.yml").read_text())
    assert "name" not in stack
    assert set(stack["services"]) == {"camera", "mediamtx", "publisher"}
    services = stack["services"]
    assert services["publisher"]["profiles"] == ["test-source"]
    assert services["camera"]["environment"]["CAMERA_URL"] == "rtsp://mediamtx:8554/cam"
    assert services["publisher"]["environment"]["SOURCE_RTSP_URL"] == "rtsp://mediamtx:8554/cam"
    assert "env_file" not in services["publisher"]
    assert "network_mode" not in services["mediamtx"]
    ncnn = yaml.safe_load((ROOT / "docker-compose.ncnn.yml").read_text())["services"]
    assert ncnn["camera"]["image"] == ncnn["publisher"]["image"]


def test_publisher_shutdown_stops_ffmpeg_and_marks_source(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    stop.wait.return_value = True
    process = Mock()
    process.poll.return_value = None
    with (
        patch.dict("os.environ", {"DATA_DIR": str(tmp_path), "SOURCE_VIDEO": str(video)}),
        patch.object(publisher.sys, "argv", ["publisher"]),
        patch.object(publisher.threading, "Event", return_value=stop),
        patch.object(publisher.signal, "signal"),
        patch.object(publisher.subprocess, "Popen", return_value=process),
    ):
        publisher.main()
    process.send_signal.assert_called_once_with(publisher.signal.SIGINT)
    process.wait.assert_called_once_with(timeout=8)
    status = json.loads((tmp_path / "source-status.json").read_text())
    assert status["mode"] == "test" and status["state"] == "stopped"


def test_missing_test_video_is_clear_startup_failure(tmp_path):
    with (
        patch.dict("os.environ", {"DATA_DIR": str(tmp_path), "SOURCE_VIDEO": str(tmp_path / "missing")}),
        patch.object(publisher.sys, "argv", ["publisher"]),
        patch.object(publisher.signal, "signal"),
        pytest.raises(SystemExit) as exc,
    ):
        publisher.main()
    assert exc.value.code == 1
    assert json.loads((tmp_path / "source-status.json").read_text())["state"] == "stopped"
