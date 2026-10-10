"""Shared thread-safe status, rotating logs and bounded side-effect workers."""

from __future__ import annotations

import copy
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time


class Status:
    def __init__(self, cfg: dict):
        self.lock = threading.Lock()
        self.values = {
            "camera": "connecting",
            "motion": False,
            "ai": "loading" if cfg["ai"]["enabled"] else "disabled",
            "ai_backend": cfg["ai"]["backend"],
            "tracked_objects": cfg["ai"]["tracked_objects"],
            "retention_days": cfg["storage"]["retention_days"],
            "min_free_disk_mb": cfg["storage"]["min_free_mb"],
            "storage_low": False,
            "device": "pending",
            "last_detection": None,
            "last_event": None,
            "ai_inference_ms": None,
            "camera_fps": 0,
            "motion_fps": 0,
            "ai_fps": 0,
            "processing_latency_ms": 0,
            "notifications_enabled": cfg["notifications"]["enabled"],
            "notification_status": "idle",
            "activity_notification_status": "idle",
            "backup_enabled": cfg["backup"]["enabled"],
            "upload_status": "idle",
            "upload_last_file": None,
            "record_events": cfg["recording"]["events"],
            "activity_analysis_enabled": cfg["activity"]["enabled"],
            "continuous_recording": cfg["recording"]["continuous"],
            "recording_status": "idle",
            "pipeline": "starting",
            "frame_size": None,
            "started_at": time.time(),
        }

    def update(self, **values):
        with self.lock:
            self.values.update(values)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.values)


def setup_logging(data_dir: Path, level: str):
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(logs / "camera.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers, force=True)


class TaskWorker:
    """Bounded queue, timeout-controlled tasks, drain on shutdown."""

    def __init__(self, name, callback, capacity=64):
        self.queue = queue.Queue(maxsize=capacity)
        self.callback = callback
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self.thread.start()

    def submit(self, item):
        if self.stopping.is_set():
            return False
        try:
            self.queue.put_nowait(item)
            return True
        except queue.Full:
            logging.getLogger(__name__).warning("side-effect queue full; task skipped")
            return False

    def _run(self):
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.callback(item)
            except Exception as exc:
                logging.getLogger(__name__).error("background task failed (%s)", type(exc).__name__)
            finally:
                self.queue.task_done()

    def close(self, timeout=15):
        self.stopping.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()
