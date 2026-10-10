import json
from unittest.mock import patch

import pytest

from app.config import ConfigError, load_config
from app.source import source_identity
from deploy.pi import source
from deploy.pi.source import camera_tool, write_status
from app.publisher import test_command as build_test_command
from tools.prepare_test_video import prepare


def configuration(tmp_path, **env):
    return load_config(
        tmp_path / "missing.yaml", {"CAMERA_URL": "rtsp://example/cam", "DATA_DIR": str(tmp_path), **env}
    )


def test_test_marker_env_default_and_validation(tmp_path):
    assert not source_identity(configuration(tmp_path))["is_test"]
    assert source_identity(configuration(tmp_path, TEST_STREAM="true"))["is_test"]
    with pytest.raises(ConfigError, match="TEST_STREAM"):
        configuration(tmp_path, TEST_STREAM="invalid")


def test_automatic_source_metadata(tmp_path):
    cfg = configuration(tmp_path)
    path = tmp_path / "source-status.json"
    write_status(path, "test", "publishing")
    assert source_identity(cfg) == {"mode": "test", "is_test": True, "publisher": "publishing"}
    write_status(path, "camera", "publishing")
    assert not source_identity(cfg)["is_test"]
    path.write_text("not json")
    assert not source_identity(cfg)["is_test"]
    path.write_text(json.dumps(["invalid"]))
    assert not source_identity(cfg)["is_test"]
    assert not list(tmp_path.glob("*.tmp"))


def test_no_camera_and_camera_probe():
    with patch("deploy.pi.source.shutil.which", return_value=None):
        assert camera_tool() is None
    with (
        patch("deploy.pi.source.shutil.which", return_value="/usr/bin/rpicam-vid"),
        patch("deploy.pi.source.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "Available cameras\n0 : imx219 [3280x2464]\n"
        run.return_value.stderr = ""
        assert camera_tool() == "/usr/bin/rpicam-vid"
        run.return_value.stdout = "No cameras available"
        assert camera_tool() is None


def test_missing_camera_clears_stale_test_identity_once(tmp_path):
    status = tmp_path / "source-status.json"
    write_status(status, "test", "stopped")
    with (
        patch.object(source, "camera_tool", return_value=None),
        patch.object(source, "write_status", wraps=write_status) as writes,
        patch.object(source.STOP, "is_set", side_effect=[False, False, True]),
        patch.object(source.STOP, "wait", return_value=False),
        patch("sys.argv", ["source.py", "--status-file", str(status)]),
    ):
        source.main()  # two probes without a camera, then stop
    assert source_identity(configuration(tmp_path)) == {"mode": "camera", "is_test": False, "publisher": "unavailable"}
    writes.assert_called_once()


def test_video_preparation_never_overwrites_input(tmp_path):
    path = tmp_path / "original.mp4"
    path.write_bytes(b"original")
    with pytest.raises(ValueError, match="already exists"):
        prepare(path, path)
    assert path.read_bytes() == b"original"
    with pytest.raises(ValueError, match="positive"):
        prepare(path, tmp_path / "output.mp4", 0)


def test_publisher_loops_without_transcoding(tmp_path):
    video = tmp_path / "source.mp4"
    with pytest.raises(ValueError, match="Missing test video"):
        build_test_command(video, "rtsp://mediamtx:8554/cam")
    video.write_bytes(b"video")
    command = build_test_command(video, "rtsp://mediamtx:8554/cam")
    assert command[command.index("-stream_loop") + 1] == "-1"
    assert command[command.index("-c:v") + 1] == "copy"
    assert "-re" in command


def test_env_file_parsing_ignores_comments_and_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nCAMERA_SIZE="1024x768"\nCAMERA_SENSOR_MODE=\nNOT A PAIR\n')
    assert source.read_env(env) == {"CAMERA_SIZE": "1024x768", "CAMERA_SENSOR_MODE": ""}
    assert source.read_env(tmp_path / "missing") == {}


def test_capture_settings_validate_and_fall_back(tmp_path, monkeypatch):
    assert source.capture_settings({}) == {
        "width": "1280",
        "height": "720",
        "mode": "",
        "tuning": "",
        "encoder": "hardware",
    }
    bad = source.capture_settings({"CAMERA_SIZE": "12x", "CAMERA_SENSOR_MODE": "--evil"})
    assert (bad["width"], bad["height"], bad["mode"]) == ("1280", "720", "")
    full = source.capture_settings({"CAMERA_SIZE": "1280x960", "CAMERA_SENSOR_MODE": "1640:1232:10:P"})
    command = source.capture_command("rpicam-vid", full)
    assert command[command.index("--mode") + 1] == "1640:1232:10:P" and command[command.index("--height") + 1] == "960"
    tuning_dir = tmp_path / "ipa"
    tuning_dir.mkdir()
    (tuning_dir / "imx219_noir.json").write_text("{}")
    monkeypatch.setattr(source, "TUNING_DIRS", (tuning_dir,))
    noir = source.capture_settings({"CAMERA_TUNING_FILE": "imx219_noir"})
    command = source.capture_command("rpicam-vid", noir)
    assert command[command.index("--tuning-file") + 1] == str(tuning_dir / "imx219_noir.json")
    assert source.capture_settings({"CAMERA_TUNING_FILE": "missing"})["tuning"] == ""


def test_software_encoder_bypasses_the_gpu_codec():
    hardware_settings = source.capture_settings({})
    hardware = source.capture_command("libcamera-vid", hardware_settings)
    assert hardware[hardware.index("--codec") + 1] == "h264" and source.encoder_command(hardware_settings) is None
    settings = source.capture_settings({"CAMERA_ENCODER": "software", "CAMERA_SIZE": "1280x960"})
    raw = source.capture_command("libcamera-vid", settings)
    assert raw[raw.index("--codec") + 1] == "yuv420"  # the GPU encoder is not used at all
    encoder = source.encoder_command(settings)
    assert encoder[encoder.index("-s") + 1] == "1280x960" and encoder[encoder.index("-c:v") + 1] == "libx264"
    assert encoder[encoder.index("-tune") + 1] == "zerolatency"  # no B-frames or lookahead latency
    assert encoder[encoder.index("-g") + 1] == "12" and encoder[-2:] == ["h264", "-"]
    assert source.capture_settings({"CAMERA_ENCODER": "gpu-ish"})["encoder"] == "hardware"
