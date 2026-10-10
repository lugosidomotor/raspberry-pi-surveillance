from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import camera as camera_module
from app.camera import RTSPCamera
from app.config import ConfigError, load_config
from app.events import EventStore
from app.settings import RuntimeSettings
from app.utils import Status
from app.web import create_app


@pytest.fixture
def cfg(tmp_path):
    return load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)})


def test_settings_default_persist_and_validate(tmp_path):
    assert RuntimeSettings(tmp_path, 90).rotation == 90
    RuntimeSettings(tmp_path).set_rotation(270)
    assert RuntimeSettings(tmp_path, 90).rotation == 270  # the UI choice outranks the env default
    for bad in (45, 360, True):
        with pytest.raises(ValueError):
            RuntimeSettings(tmp_path).set_rotation(bad)
    (tmp_path / "settings.json").write_text("{broken")
    assert RuntimeSettings(tmp_path, 180).rotation == 180
    assert not list(tmp_path.glob("*.tmp"))


def test_rotation_env_is_validated(tmp_path):
    base = {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)}
    assert load_config(tmp_path / "none", {**base, "CAMERA_ROTATION": "90"})["camera"]["rotation"] == 90
    with pytest.raises(ConfigError, match="CAMERA_ROTATION"):
        load_config(tmp_path / "none", {**base, "CAMERA_ROTATION": "45"})


def test_camera_turns_frames_and_starts_new_generation_on_change(cfg, monkeypatch):
    angles = iter([0, 90, 90])
    camera = RTSPCamera(cfg["camera"], rotation=lambda: next(angles))
    seen = []

    class FakeCapture:
        def __init__(self, url, api, params):
            self.reads = 0

        def isOpened(self):
            return True

        def read(self):
            self.reads += 1
            if self.reads > 3:
                camera._stop.set()
                return False, None
            return True, np.zeros((4, 6, 3), np.uint8)

        def release(self):
            pass

    original = camera._condition.notify_all

    def record():
        if camera._latest is not None:  # also called on disconnect, after the slot is cleared
            seen.append((camera._latest.image.shape, camera._latest.generation))
        original()

    monkeypatch.setattr(camera_module.cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(camera._condition, "notify_all", record)
    camera._run()
    assert seen[:3] == [((4, 6, 3), 1), ((6, 4, 3), 2), ((6, 4, 3), 2)]


def test_rotation_api_changes_and_reports_setting(cfg, tmp_path):
    store = EventStore(tmp_path)
    rt = SimpleNamespace(
        status=Status(cfg), store=store, settings=RuntimeSettings(tmp_path), start=lambda: None, close=lambda: None
    )
    with TestClient(create_app(cfg, rt)) as client:
        assert client.get("/api/status").json()["rotation"] == 0
        assert client.post("/api/settings/rotation", json={"rotation": 90}).json() == {"rotation": 90}
        assert client.get("/api/status").json()["rotation"] == 90
        assert client.post("/api/settings/rotation", json={"rotation": 45}).status_code == 422
        # A cross-site "simple" request cannot send JSON; a text body must not be accepted.
        plain = client.post(
            "/api/settings/rotation", content='{"rotation": 180}', headers={"Content-Type": "text/plain"}
        )
        assert plain.status_code == 422
    assert RuntimeSettings(tmp_path).rotation == 90
    store.close()
