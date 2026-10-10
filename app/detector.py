"""Backend-neutral contract. Only this adapter knows about Ultralytics/Torch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
from pathlib import Path

import yaml
import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[int, int, int, int]


class ObjectDetector(ABC):
    @abstractmethod
    def detect(self, frame) -> list[Detection]:
        """Return pixel xyxy boxes relative to the provided BGR frame."""


def select_device(requested: str, model: str) -> str:
    # Exported CPU runtimes have different accelerator support from PyTorch.
    if not model.lower().endswith(".pt"):
        if requested != "cpu":
            log.info("exported model: using CPU runtime")
        return "cpu"
    if requested != "auto":
        return "0" if requested == "cuda" else requested
    import torch

    if torch.cuda.is_available():
        return "0"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class UltralyticsDetector(ObjectDetector):
    def __init__(self, cfg: dict):
        from ultralytics import YOLO

        self.cfg = cfg
        self.device = select_device(cfg["device"], cfg["model"])
        self.model = YOLO(cfg["model"], task="detect")
        # Export metadata provides class names without initializing NCNN twice.
        names = (
            yaml.safe_load((Path(cfg["model"]) / "metadata.yaml").read_text())["names"]
            if cfg["backend"] == "ncnn"
            else self.model.names
        )
        requested = set(cfg["tracked_objects"])
        self.classes = [i for i, name in names.items() if name in requested]
        missing = requested - set(names.values())
        if missing:
            raise ValueError(f"Model does not contain requested classes: {sorted(missing)}")
        self.minimum_confidence = min(cfg["confidence_by_class"].get(name, cfg["confidence"]) for name in requested)
        log.info("detector loaded: backend=%s device=%s classes=%s", cfg["backend"], self.device, sorted(requested))

    def detect(self, frame) -> list[Detection]:
        kwargs = dict(
            source=frame,
            imgsz=self.cfg["image_size"],
            conf=self.minimum_confidence,
            classes=self.classes,
            device=self.device,
            verbose=False,
        )
        if self.cfg["backend"] == "ncnn":
            kwargs["rect"] = False  # Match the square input shape used at export.
        try:
            result = self.model.predict(**kwargs)[0]
        except (RuntimeError, NotImplementedError):
            if self.cfg["device"] != "auto" or self.device == "cpu":
                raise
            log.warning("accelerator inference failed; switching to CPU")
            self.device = "cpu"
            # A cached predictor may already own an accelerator model.
            self.model.to("cpu")
            self.model.predictor = None
            kwargs["device"] = "cpu"
            result = self.model.predict(**kwargs)[0]
        detections = []
        if result.boxes is None:
            return detections
        height, width = frame.shape[:2]
        for box, confidence, class_id in zip(
            result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()
        ):
            label = result.names[int(class_id)]
            if confidence < self.cfg["confidence_by_class"].get(label, self.cfg["confidence"]):
                continue
            x1, y1, x2, y2 = box
            coords = (
                max(0, min(width - 1, int(x1))),
                max(0, min(height - 1, int(y1))),
                max(0, min(width, int(x2))),
                max(0, min(height, int(y2))),
            )
            if coords[2] > coords[0] and coords[3] > coords[1]:
                detections.append(Detection(label, float(confidence), coords))
        return detections


class NCNNDetector(ObjectDetector):
    """Native CPU NCNN for standard YOLO11 detect exports; no Torch runtime overhead."""

    def __init__(self, cfg: dict):
        model = Path(cfg["model"])
        if not model.is_dir() or not (model / "model.ncnn.param").is_file() or not (model / "model.ncnn.bin").is_file():
            raise ValueError("NCNN backend requires an exported model directory with param and bin files")
        metadata = yaml.safe_load((model / "metadata.yaml").read_text())
        if not isinstance(metadata, dict) or metadata.get("imgsz") != [cfg["image_size"], cfg["image_size"]]:
            raise ValueError("AI_IMAGE_SIZE must match the NCNN model export size")
        if cfg["device"] not in ("auto", "cpu"):
            raise ValueError("This NCNN adapter supports CPU only")
        import ncnn

        self.ncnn, self.cfg, self.device = ncnn, cfg, "cpu"
        self.names = metadata["names"]
        if isinstance(self.names, list):
            self.names = dict(enumerate(self.names))
        if metadata.get("task") != "detect" or metadata.get("end2end", False):
            raise ValueError("NCNN adapter requires a standard YOLO detect export without embedded NMS")
        self.classes = {i for i, name in self.names.items() if name in cfg["tracked_objects"]}
        if set(cfg["tracked_objects"]) - set(self.names.values()):
            raise ValueError("NCNN model is missing requested classes")
        self.net = ncnn.Net()
        self.net.opt.num_threads = cfg["cpu_threads"]
        self.net.opt.use_vulkan_compute = False
        self.net.opt.use_fp16_storage = False
        self.net.opt.use_fp16_arithmetic = False
        if (
            self.net.load_param(str(model / "model.ncnn.param")) != 0
            or self.net.load_model(str(model / "model.ncnn.bin")) != 0
        ):
            raise ValueError("Could not load NCNN model")
        log.info(
            "detector loaded: backend=ncnn device=cpu threads=%s classes=%s",
            cfg["cpu_threads"],
            sorted(cfg["tracked_objects"]),
        )

    def detect(self, frame) -> list[Detection]:
        height, width = frame.shape[:2]
        size = self.cfg["image_size"]
        gain = min(size / width, size / height)
        scaled_width, scaled_height = round(width * gain), round(height * gain)
        left, top = (size - scaled_width) // 2, (size - scaled_height) // 2
        image = cv2.resize(frame, (scaled_width, scaled_height))
        image = cv2.copyMakeBorder(
            image,
            top,
            size - scaled_height - top,
            left,
            size - scaled_width - left,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        tensor = np.ascontiguousarray(image[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32) / 255
        with self.net.create_extractor() as extractor:
            if extractor.input(self.net.input_names()[0], self.ncnn.Mat(tensor)) != 0:
                raise RuntimeError("NCNN input failed")
            code, output = extractor.extract(self.net.output_names()[0])
            if code != 0:
                raise RuntimeError("NCNN inference failed")
            predictions = np.array(output, dtype=np.float32, copy=True)
        channels = 4 + len(self.names)
        if predictions.ndim != 2:
            raise RuntimeError("Unexpected NCNN output shape")
        if predictions.shape[0] == channels:
            predictions = predictions.T
        elif predictions.shape[1] != channels:
            raise RuntimeError("Unexpected NCNN output channels")
        class_ids = predictions[:, 4:].argmax(axis=1)
        scores = predictions[np.arange(len(predictions)), class_ids + 4]
        detections = []
        for class_id in sorted(self.classes):
            label = self.names[class_id]
            threshold = self.cfg["confidence_by_class"].get(label, self.cfg["confidence"])
            mask = (class_ids == class_id) & (scores >= threshold) & np.isfinite(predictions).all(axis=1)
            boxes = predictions[mask, :4].copy()
            confidences = scores[mask]
            if not len(boxes):
                continue
            boxes[:, :2] -= boxes[:, 2:] / 2  # cx,cy,w,h → x,y,w,h
            keep = np.asarray(cv2.dnn.NMSBoxes(boxes.tolist(), confidences.tolist(), threshold, 0.7)).reshape(-1)
            for index in keep:
                x, y, w, h = boxes[index]
                coords = (
                    max(0, min(width - 1, int((x - left) / gain))),
                    max(0, min(height - 1, int((y - top) / gain))),
                    max(0, min(width, int((x + w - left) / gain))),
                    max(0, min(height, int((y + h - top) / gain))),
                )
                if coords[2] > coords[0] and coords[3] > coords[1]:
                    detections.append(Detection(label, float(confidences[index]), coords))
        return sorted(detections, key=lambda detection: detection.confidence, reverse=True)[:300]


def create_detector(cfg: dict) -> ObjectDetector:
    if cfg["backend"] == "ncnn":
        return NCNNDetector(cfg)
    if cfg["backend"] == "ultralytics":
        return UltralyticsDetector(cfg)
    raise ValueError(f"Unknown detector backend: {cfg['backend']}")
