"""Person-episode montage: evenly spaced frames from the first person event until motion stays quiet."""

from __future__ import annotations

from datetime import datetime
import logging
import math
import time

import cv2
import numpy as np

from .utils import TaskWorker

log = logging.getLogger(__name__)
HEADER_HEIGHT = 40


def compose(tiles, header):
    """Grid of (timestamp, image) tiles under a one-line header; every tile takes the first tile's size."""
    height, width = tiles[0][1].shape[:2]
    columns = min(4, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    canvas = np.full((HEADER_HEIGHT + rows * height, columns * width, 3), 24, dtype=np.uint8)
    cv2.putText(canvas, header, (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2, cv2.LINE_AA)
    for index, (stamp, image) in enumerate(tiles):
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height))
        top, left = HEADER_HEIGHT + index // columns * height, index % columns * width
        canvas[top : top + height, left : left + width] = image
        label = stamp.strftime("%H:%M:%S")
        cv2.rectangle(canvas, (left, top + height - 30), (left + 112, top + height), (0, 0, 0), -1)
        cv2.putText(
            canvas, label, (left + 8, top + height - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA
        )
    return canvas


class MontageRecorder:
    """Collects frames on the pipeline thread; composing, saving and notifying run on a worker."""

    def __init__(self, cfg, store, status, notifier=None):
        self.cfg, self.store, self.status, self.notifier = cfg["montage"], store, status, notifier
        self.worker = TaskWorker("montage", self._finish, capacity=4) if self.cfg["enabled"] else None
        self.session = None

    @property
    def enabled(self):
        return self.worker is not None

    def start(self):
        if self.worker:
            self.worker.start()

    def begin(self, event):
        """Start an episode on a person event; later events of the same episode only join it."""
        if not self.worker:
            return
        if self.session:
            self.session["event_ids"].append(event["id"])
            # One alert anywhere in the episode makes its montage worth a notification.
            self.session["alert"] = self.session["alert"] or event.get("alert", True)
            self.store.update(event["id"], montage_status="joined", montage_event_id=self.session["event"]["id"])
            return
        self.session = {
            "event": event,
            "event_ids": [event["id"]],
            "tiles": [],
            "interval": self.cfg["interval_seconds"],
            "next_at": float("-inf"),
            "started": time.monotonic(),
            "last_active": None,
            "alert": event.get("alert", True),
        }

    def feed(self, frame, tick, active):
        """Called for every processed frame; ``active`` is false once motion (incl. its cooldown) has ended."""
        session = self.session
        if session is None:
            return
        if frame is None or tick - session["started"] >= self.cfg["max_seconds"]:
            self.end()
            return
        if active or session["last_active"] is None:
            session["last_active"] = tick
        elif tick - session["last_active"] >= self.cfg["idle_seconds"]:
            # Someone returning within the idle window continues the same montage instead of being lost
            # inside the event cooldown; quiet periods add no empty frames.
            self.end()
            return
        if not active or tick < session["next_at"]:
            return
        session["next_at"] = tick + session["interval"]
        height, width = frame.shape[:2]
        tile_width = min(width, self.cfg["tile_width"])
        tile = cv2.resize(frame, (tile_width, max(1, round(height * tile_width / width))))
        session["tiles"].append((datetime.now().astimezone(), tile))
        if len(session["tiles"]) > self.cfg["max_frames"]:
            # Halve and slow down: memory stays bounded and frames stay evenly spread over long episodes.
            session["tiles"] = session["tiles"][::2]
            session["interval"] *= 2

    def end(self):
        session, self.session = self.session, None
        if session is None:
            return
        if not session["tiles"]:
            self.store.update(session["event"]["id"], montage_status="failed")
        elif not self.worker.submit(session):
            self.store.update(session["event"]["id"], montage_status="queue_full")

    def _finish(self, session):
        event = session["event"]
        tiles = session["tiles"]
        start, end = tiles[0][0], tiles[-1][0]
        header = (
            f"{event['label']} | {event['zone']} | {start:%Y-%m-%d %H:%M:%S} - {end:%H:%M:%S} | {len(tiles)} frames"
        )
        if event.get("is_test"):
            header += " | TEST STREAM"
        path = self.store.root / event["snapshot_path"]
        path = path.with_name(f"{event['id']}_montage.jpg")
        temporary = path.with_suffix(".tmp")
        try:
            ok, encoded = cv2.imencode(".jpg", compose(tiles, header), [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                raise OSError("JPEG encoding failed")
            temporary.write_bytes(encoded.tobytes())
            temporary.replace(path)
            self.store.register_media(path, "montage")
        except (OSError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            self.store.update(event["id"], montage_status="failed")
            log.error("montage failed: %s (%s)", event["id"], type(exc).__name__)
            return
        relative = str(path.relative_to(self.store.root))
        for event_id in session["event_ids"]:  # every event of the episode links the same montage
            self.store.update(event_id, montage_status="completed", montage_path=relative, montage_frames=len(tiles))
        log.info("montage saved: %s (%d frames, %s-%s)", relative, len(tiles), f"{start:%H:%M:%S}", f"{end:%H:%M:%S}")
        if not session["alert"]:
            self.store.update(event["id"], montage_notification_status="silent")
        elif self.notifier:
            self.notifier.send_montage(event, path, len(tiles), start, end)
        else:
            self.store.update(event["id"], montage_notification_status="disabled")

    def close(self, timeout=15):
        if not self.worker:
            return True
        self.end()  # an episode still open at shutdown is saved rather than dropped
        return self.worker.close(timeout)
