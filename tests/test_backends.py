from unittest.mock import MagicMock, patch

import pytest
import numpy as np

from app.camera import RTSPCamera
from app.config import load_config
from app.detector import NCNNDetector, create_detector


def test_reconnect_cap_for_local_and_public_streams(tmp_path):
    cfg = load_config(tmp_path / "missing", {"CAMERA_URL": "rtsp://example/cam"})
    camera = RTSPCamera(cfg["camera"])
    assert [camera.reconnect_delay(i) for i in range(8)] == [1, 2, 5, 10, 20, 30, 30, 30]
    cfg = load_config(tmp_path / "missing", {"CAMERA_URL": "rtsp://example/cam", "CAMERA_RECONNECT_MAX_SECONDS": "2"})
    camera = RTSPCamera(cfg["camera"])
    assert [camera.reconnect_delay(i) for i in range(8)] == [1, 2, 2, 2, 2, 2, 2, 2]


def test_ncnn_config_and_model_contract(tmp_path):
    model = tmp_path / "yolo11n_ncnn_model"
    cfg = load_config(
        tmp_path / "missing", {"CAMERA_URL": "rtsp://example/cam", "AI_BACKEND": "ncnn", "AI_MODEL": str(model)}
    )["ai"]
    with pytest.raises(ValueError, match="exported model"):
        create_detector(cfg)
    model.mkdir()
    (model / "model.ncnn.param").write_text("test")
    (model / "model.ncnn.bin").write_bytes(b"test")
    (model / "metadata.yaml").write_text("imgsz: [320, 320]\ntask: detect\nnames: {0: person}\n")
    native = MagicMock()
    native.Net.return_value.load_param.return_value = 0
    native.Net.return_value.load_model.return_value = 0
    with patch.dict("sys.modules", {"ncnn": native}):
        assert isinstance(create_detector(cfg), NCNNDetector)
        assert native.Net.return_value.opt.num_threads == 2
        assert native.Net.return_value.opt.use_fp16_arithmetic is False
    with pytest.raises(ValueError, match="export size"):
        create_detector(dict(cfg, image_size=640))
    with pytest.raises(ValueError, match="CPU only"):
        create_detector(dict(cfg, device="cuda"))


def test_native_ncnn_filter_nms_and_letterbox_coordinates():
    detector = NCNNDetector.__new__(NCNNDetector)
    detector.cfg = {"image_size": 320, "confidence": 0.75, "confidence_by_class": {}}
    detector.names, detector.classes = {0: "person", 1: "dog"}, {0}
    detector.ncnn, detector.net = MagicMock(), MagicMock()
    extractor = detector.net.create_extractor.return_value.__enter__.return_value
    extractor.input.return_value = 0
    rows = np.array(
        [
            [125, 170, 50, 100, 0.9, 0.1],
            [125, 170, 50, 100, 0.8, 0.1],
            [200, 170, 50, 100, 0.1, 0.95],
            [250, 170, 50, 100, 0.74, 0.1],
        ],
        dtype=np.float32,
    )
    extractor.extract.return_value = (0, rows.T)
    detections = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
    assert len(detections) == 1
    assert detections[0].label == "person"
    assert detections[0].confidence == pytest.approx(0.9)
    assert detections[0].box == (200, 100, 300, 300)
    tensor = detector.ncnn.Mat.call_args.args[0]
    assert tensor.shape == (3, 320, 320) and tensor.dtype == np.float32
    assert tensor[0, 0, 0] == pytest.approx(114 / 255)
    extractor.extract.return_value = (0, np.zeros((6, 1), dtype=np.float32))
    assert detector.detect(np.zeros((360, 640, 3), dtype=np.uint8)) == []
    extractor.extract.return_value = (-1, None)
    with pytest.raises(RuntimeError, match="inference failed"):
        detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
