"""Cheap grayscale MOG2 motion gate; boxes use original frame coordinates."""

from __future__ import annotations

from dataclasses import dataclass
import cv2


@dataclass
class MotionResult:
    confirmed: bool
    boxes: list[tuple[int, int, int, int]]


class MotionDetector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.background = cv2.createBackgroundSubtractorMOG2(
            history=self.cfg["history"], varThreshold=self.cfg["threshold"], detectShadows=False
        )
        self.frames = self.consecutive = 0
        self.shape = None
        self.next_sample = 0

    def sample(self, frame, now: float) -> MotionResult | None:
        """Process latest frames at a bounded rate, never catch up on skipped frames."""
        if now < self.next_sample:
            return None
        result = self.process(frame)
        self.next_sample = now + 1 / self.cfg["max_fps"]
        return result

    def process(self, frame) -> MotionResult:
        height, width = frame.shape[:2]
        if self.shape is not None and self.shape != (height, width):
            self.reset()
        self.shape = (height, width)
        target_width = min(width, self.cfg["resize_width"])
        target_height = max(1, round(height * target_width / width))
        small = cv2.resize(frame, (target_width, target_height))
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        mask = self.background.apply(gray, learningRate=self.cfg["learning_rate"])
        mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        mask = cv2.dilate(mask, None, iterations=2)
        self.frames += 1
        if self.frames <= self.cfg["warmup_frames"]:
            return MotionResult(False, [])
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            if cv2.contourArea(contour) < self.cfg["min_area"]:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            boxes.append(
                (
                    int(x * width / target_width),
                    int(y * height / target_height),
                    min(width, round((x + w) * width / target_width)),
                    min(height, round((y + h) * height / target_height)),
                )
            )
        self.consecutive = self.consecutive + 1 if boxes else 0
        return MotionResult(self.consecutive >= self.cfg["required_frames"], boxes)


def padded_crop(frame, boxes, padding: float):
    if not boxes:
        return frame, (0, 0)
    height, width = frame.shape[:2]
    x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    px, py = int((x2 - x1) * padding), int((y2 - y1) * padding)
    x1, y1 = max(0, x1 - px), max(0, y1 - py)
    x2, y2 = min(width, x2 + px), min(height, y2 + py)
    return frame[y1:y2, x1:x2], (x1, y1)
