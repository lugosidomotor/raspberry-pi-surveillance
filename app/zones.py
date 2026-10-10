"""Normalized rectangle/polygon zones; ordered per-class rule selection."""

from __future__ import annotations

from dataclasses import dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class ZoneMatch:
    name: str
    action: str


class ZoneManager:
    def __init__(self, configs: list[dict]):
        self.zones = []
        names = set()
        for cfg in configs:
            if not isinstance(cfg, dict):
                raise ValueError("Each zone must be a mapping")
            name = cfg.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("Zone names must be nonempty and unique")
            names.add(name)
            if ("rectangle" in cfg) == ("polygon" in cfg):
                raise ValueError(f"Zone {name}: specify exactly one rectangle or polygon")
            if "rectangle" in cfg:
                rect = cfg["rectangle"]
                if not isinstance(rect, list) or len(rect) != 4:
                    raise ValueError(f"Zone {name}: rectangle needs [x1,y1,x2,y2]")
                x1, y1, x2, y2 = rect
                try:
                    valid = 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1
                except TypeError:
                    valid = False
                if not valid:
                    raise ValueError(f"Zone {name}: invalid normalized rectangle")
                points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            else:
                points = cfg["polygon"]
            try:
                polygon = np.asarray(points, dtype=np.float32)
                valid = (
                    polygon.ndim == 2
                    and polygon.shape[1] == 2
                    and len(polygon) >= 3
                    and np.isfinite(polygon).all()
                    and (polygon >= 0).all()
                    and (polygon <= 1).all()
                    and abs(cv2.contourArea(polygon)) > 1e-8
                )
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise ValueError(f"Zone {name}: polygon needs >=3 nondegenerate normalized points")
            rules = cfg.get("rules", {})
            if not isinstance(rules, dict) or any(
                action not in ("ignore", "event", "notify", "record", "snapshot") for action in rules.values()
            ):
                raise ValueError(f"Zone {name}: invalid rules")
            self.zones.append((name, polygon, rules))

    def match(self, detection, frame_shape) -> ZoneMatch | None:
        if not self.zones:
            return ZoneMatch("full_frame", "snapshot")
        height, width = frame_shape[:2]
        x1, _, x2, y2 = detection.box
        point = ((x1 + x2) / (2 * width), min(1.0, y2 / height))
        for name, polygon, rules in self.zones:
            action = rules.get(detection.label)
            if action is not None and cv2.pointPolygonTest(polygon, point, False) >= 0:
                return None if action == "ignore" else ZoneMatch(name, action)
        return None

    def draw(self, image) -> None:
        height, width = image.shape[:2]
        for name, polygon, _ in self.zones:
            points = np.rint(polygon * [width - 1, height - 1]).astype(np.int32)
            cv2.polylines(image, [points], True, (220, 160, 40), 2)
            cv2.putText(image, name, tuple(points[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 160, 40), 1)
