"""N-of-M AI votes per (class, zone). No individual identity/tracking assumption."""

from __future__ import annotations

from collections import deque


def _confidence(value) -> float:
    detection = value[0] if isinstance(value, tuple) else value
    return getattr(detection, "confidence", 0.0)


class TemporalValidator:
    def __init__(self, cfg: dict):
        self.required = cfg["positive_frames"]
        self.window = cfg["window_frames"]
        self.max_gap = cfg["max_gap_seconds"]
        # 0 disables the fast path; otherwise one frame this confident is enough on its own.
        self.instant = cfg.get("instant_confidence", 0)
        self.history = deque(maxlen=self.window)
        self.last_at: float | None = None

    def reset(self) -> None:
        self.history.clear()
        self.last_at = None

    def update(self, candidates: dict, now: float) -> dict:
        if self.last_at is not None and now - self.last_at > self.max_gap:
            self.reset()
        self.last_at = now
        self.history.append(set(candidates))  # a negative AI frame is an empty set
        # Require a positive observation in this frame too; snapshots never use stale boxes.
        return {
            key: value
            for key, value in candidates.items()
            if sum(key in votes for votes in self.history) >= self.required
            or (self.instant and _confidence(value) >= self.instant)
        }
