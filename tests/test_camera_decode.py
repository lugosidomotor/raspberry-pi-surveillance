import os
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest
import yaml

from app import camera as camera_module
from app import events as events_module
from app.camera import RTSPCamera
from app.config import ConfigError, load_config
from app.detector import Detection
from app.events import EventManager, EventStore
from app.utils import Status
from app.zones import ZoneManager

ROOT = Path(__file__).resolve().parents[1]
HW = "rtsp_transport;tcp|video_codec;h264_v4l2m2m"
SW = "rtsp_transport;tcp"


def camera_cfg(tmp_path, **env):
    base = {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path), "CAMERA_DECODER": "h264_v4l2m2m"}
    return load_config(tmp_path / "none", {**base, **env})["camera"]


def fake_capture(camera, opens, software_works=True):
    class FakeCapture:
        def __init__(self, url, api, params):
            self.options = os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
            opens.append(self.options)
            self.reads = 0

        def isOpened(self):
            return "video_codec" not in self.options and software_works

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, np.zeros((4, 4, 3), np.uint8)
            camera._stop.set()
            return False, None

        def release(self):
            pass

    return FakeCapture


def test_missing_hardware_decoder_falls_back_to_software(tmp_path, monkeypatch):
    camera = RTSPCamera(camera_cfg(tmp_path))
    opens = []
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", fake_capture(camera, opens))
    camera._run()
    assert opens == [HW, SW]
    assert camera.frame_count == 1


def test_stream_outage_keeps_hardware_decoder_for_next_attempt(tmp_path, monkeypatch):
    camera = RTSPCamera(camera_cfg(tmp_path))
    opens = []
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", fake_capture(camera, opens, software_works=False))
    waits = iter([False, True])
    monkeypatch.setattr(camera._stop, "wait", lambda delay: camera._stop.set() if next(waits) else None)
    camera._run()
    assert opens == [HW, SW, HW, SW]  # software also failed, so the stream was down, not the decoder


def test_software_decoding_is_default_and_decoder_name_is_validated(tmp_path):
    assert (
        load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://e/l", "DATA_DIR": str(tmp_path)})["camera"]["decoder"]
        == ""
    )
    with pytest.raises(ConfigError, match="CAMERA_DECODER"):
        camera_cfg(tmp_path, CAMERA_DECODER="h264;rtsp_transport")


def test_hardware_decode_override_shares_only_the_decoder_node():
    override = yaml.safe_load((ROOT / "docker-compose.hwdecode.yml").read_text())["services"]["camera"]
    assert override["devices"] == ["/dev/video10:/dev/video10"]
    assert override["group_add"] == ["44"]
    assert override["environment"]["CAMERA_DECODER"].endswith("h264_v4l2m2m}")
    assert "privileged" not in override
    # The general Pi override must stay valid on models without this decoder (e.g. Pi 5).
    assert "devices" not in yaml.safe_load((ROOT / "docker-compose.pi.yml").read_text())["services"]["camera"]


def test_snapshot_uses_quality_85(tmp_path):
    cfg = load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://e/l", "DATA_DIR": str(tmp_path)})
    store = EventStore(tmp_path)
    manager = EventManager(cfg, store, Mock(), None, Status(cfg))
    detection = Detection("person", 0.9, (10, 10, 100, 100))
    with patch.object(events_module.cv2, "imencode", wraps=cv2.imencode) as encode:
        manager.emit(np.zeros((120, 160, 3), np.uint8), detection, ZoneManager([]).match(detection, (120, 160, 3)), 0)
    assert encode.call_args_list[0].args[2] == [cv2.IMWRITE_JPEG_QUALITY, 85]
    store.close()
