"""One inference thread; HTTP, notification and backup never block camera processing."""

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import platform
import threading
import time

from .camera import RTSPCamera
from .activity import ActivityAnalyzer
from .detector import Detection, create_detector
from .events import EventManager, EventStore
from .montage import MontageRecorder
from .motion import MotionDetector, padded_crop
from .settings import RuntimeSettings
from .notifier import NtfyNotifier
from .recorder import ContinuousRecorder, EventRecorder
from .uploader import RcloneUploader
from .utils import Status, TaskWorker
from .validator import TemporalValidator
from .zones import ZoneManager
from .storage import StoragePolicy

log = logging.getLogger(__name__)


def detector_config(ai):
    """The detector must return silent-tier boxes too; alert thresholds are applied afterwards."""
    record = ai["record_confidence"]
    if not record:
        return ai
    return dict(
        ai,
        confidence=min(ai["confidence"], record),
        confidence_by_class={label: min(value, record) for label, value in ai["confidence_by_class"].items()},
    )


def tiered_candidates(detections, ai):
    """Best (detection, zone) per key: all recorded ones, and the subset that reaches the alert threshold."""
    recorded, alerting = {}, {}
    for d, zone in detections:
        key = (d.label, zone.name)
        if key not in recorded or d.confidence > recorded[key][0].confidence:
            recorded[key] = (d, zone)
        if d.confidence >= ai["confidence_by_class"].get(d.label, ai["confidence"]):
            if key not in alerting or d.confidence > alerting[key][0].confidence:
                alerting[key] = (d, zone)
    return recorded, alerting


class Runtime:
    def __init__(self, cfg):
        self.cfg = cfg
        self.status = Status(cfg)
        self.status.update(cpu=f"{platform.machine()} / {platform.system()}")
        self.store = EventStore(Path(cfg["data_dir"]))
        self.settings = RuntimeSettings(Path(cfg["data_dir"]), cfg["camera"]["rotation"])
        self.camera = RTSPCamera(cfg["camera"], lambda: self.settings.rotation)
        self.recorder = EventRecorder(cfg, self.store, self.status)
        self.continuous = ContinuousRecorder(cfg, self.store)
        self.storage = StoragePolicy(cfg["storage"], self.store, self.status)
        notifier = (
            NtfyNotifier(cfg["notifications"], self.store, self.status) if cfg["notifications"]["enabled"] else None
        )
        self.notifications = TaskWorker("notifications", notifier.send) if notifier else None
        self.activity = ActivityAnalyzer(cfg, self.store, self.status, notifier)
        self.montage = MontageRecorder(cfg, self.store, self.status, notifier)
        self.uploader = RcloneUploader(cfg["backup"], self.store, self.status) if cfg["backup"]["enabled"] else None
        self.events = EventManager(
            cfg, self.store, self.recorder, self.notifications, self.status, self.activity, self.montage
        )
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="camera-pipeline", daemon=True)

    def start(self):
        for feature, enabled in [
            ("Camera", True),
            ("Motion detection", self.cfg["motion"]["enabled"]),
            ("AI detection", self.cfg["ai"]["enabled"]),
            ("Notifications", self.notifications is not None),
            ("Video activity analysis", self.activity.enabled),
            ("Person montage", self.montage.enabled),
            ("Cloud backup", self.uploader is not None),
            ("Web UI", self.cfg["web"]["enabled"]),
        ]:
            log.info("%s: %s", feature, "enabled" if enabled else "disabled")
        if self.notifications:
            self.notifications.start()
        self.activity.start()
        self.montage.start()
        if self.uploader:
            self.uploader.start()
        self.camera.start()
        self.thread.start()
        log.info("application started")
        if self.cfg["motion"]["enabled"]:
            log.info("motion sampling limited to %.1f FPS", self.cfg["motion"]["max_fps"])

    def _run(self):
        try:
            failures = 0
            while not self.stop.is_set():
                try:
                    self._pipeline()
                    break
                except Exception as exc:
                    failures += 1
                    delay = min(30, 2 ** min(failures - 1, 5))
                    self.status.update(pipeline="failed", ai="error")
                    log.error("pipeline failed (%s); retry in %ss", type(exc).__name__, delay)
                    self.recorder.close()
                    self.continuous.close()
                    self.stop.wait(delay)
        finally:
            self.recorder.close()
            self.continuous.close()

    def _pipeline(self):
        detector = create_detector(detector_config(self.cfg["ai"])) if self.cfg["ai"]["enabled"] else None
        self.status.update(
            ai="ready" if detector else "disabled", device=detector.device if detector else "CPU", pipeline="running"
        )
        motion = MotionDetector(self.cfg["motion"])
        validator = TemporalValidator(self.cfg["validation"])
        # Silent tier: same N-of-M voting, never the single-frame fast path.
        silent_validator = TemporalValidator(dict(self.cfg["validation"], instant_confidence=0))
        zones = ZoneManager(self.cfg["zones"])
        seq, generation, shape = 0, None, None
        gate_until = next_ai = 0
        last_boxes = []
        result = None
        last_motion_log = float("-inf")
        stats_at = time.monotonic()
        previous_camera_count = motion_count = ai_count = processed = 0
        inference_total = latency_total = 0
        while not self.stop.is_set():
            self.recorder.poll()
            writable = self.storage.poll()
            if writable:
                self.continuous.poll()
                self.recorder.poll_buffer()
            else:
                self.continuous.close()
                self.recorder.close()
            packet = self.camera.read(seq)
            self.status.update(camera="connected" if self.camera.connected else "disconnected")
            if packet is None:
                if not self.camera.connected:
                    validator.reset()
                    silent_validator.reset()
                    gate_until = 0
                    self.status.update(motion=False)
                    self.montage.end()
                continue
            seq = packet.sequence
            frame = packet.image
            tick = time.monotonic()
            if packet.generation != generation or frame.shape != shape:
                generation, shape = packet.generation, frame.shape
                self.status.update(frame_size=[frame.shape[1], frame.shape[0]])  # the UI sizes the viewer by it
                motion.reset()
                validator.reset()
                silent_validator.reset()
                gate_until = next_ai = 0
                last_boxes = []
                result = None
            sample = motion.sample(frame, tick) if self.cfg["motion"]["enabled"] else None
            if sample is not None:
                result = sample
                motion_count += 1
            boxes = result.boxes if result else []
            if sample and sample.confirmed:
                gate_until = tick + self.cfg["motion"]["cooldown_seconds"]
                last_boxes = boxes
                if tick - last_motion_log >= max(1, self.cfg["motion"]["cooldown_seconds"]):
                    log.info("motion detected: %s regions", len(boxes))
                    last_motion_log = tick
            gate = not self.cfg["motion"]["enabled"] or tick < gate_until or bool(result and result.confirmed)
            self.status.update(motion=bool(result and result.confirmed))
            if not gate:
                validator.reset()
                silent_validator.reset()
            if detector and gate and tick >= next_ai:
                next_ai = tick + 1 / self.cfg["ai"]["max_fps"]
                region, offset = frame, (0, 0)
                if self.cfg["motion"]["enabled"] and self.cfg["ai"]["crop_motion"]:
                    region, offset = padded_crop(frame, boxes or last_boxes, self.cfg["motion"]["crop_padding"])
                inference_start = time.monotonic()
                try:
                    detections = detector.detect(region)
                    self.status.update(ai="ready", device=detector.device)
                except Exception as exc:
                    log.error("AI inference failed (%s); retry in 5 sec", type(exc).__name__)
                    self.status.update(ai="error")
                    validator.reset()
                    silent_validator.reset()
                    next_ai = time.monotonic() + 5
                    detections = None
                inference_ms = (time.monotonic() - inference_start) * 1000
                self.status.update(ai_inference_ms=round(inference_ms, 1))
                inference_total += inference_ms
                ai_count += 1
                log.debug("AI inference time: %.1f ms", inference_ms)
                if detections is not None:
                    ox, oy = offset
                    matched = []
                    for item in detections:
                        d = Detection(
                            item.label,
                            item.confidence,
                            (item.box[0] + ox, item.box[1] + oy, item.box[2] + ox, item.box[3] + oy),
                        )
                        alert_threshold = self.cfg["ai"]["confidence_by_class"].get(
                            d.label, self.cfg["ai"]["confidence"]
                        )
                        if d.confidence >= alert_threshold:  # the UI keeps showing alert-level detections only
                            self.status.update(
                                last_detection={
                                    "label": d.label,
                                    "confidence": d.confidence,
                                    "timestamp": datetime.now().astimezone().isoformat(),
                                }
                            )
                        log.debug("object detected: %s %.2f", d.label, d.confidence)
                        zone = zones.match(d, frame.shape)
                        if zone:
                            matched.append((d, zone))
                    recorded, alerting = tiered_candidates(matched, self.cfg["ai"])
                    if self.packet_is_current(packet):
                        timestamp = datetime.now(timezone.utc) - timedelta(
                            seconds=time.monotonic() - packet.captured_at
                        )
                        alerts = validator.update(alerting, tick)
                        silent = silent_validator.update(recorded, tick) if self.cfg["ai"]["record_confidence"] else {}
                        emits = [(d, zone, True) for d, zone in alerts.values()]
                        emits += [(d, zone, False) for key, (d, zone) in silent.items() if key not in alerts]
                        for d, zone, alert in emits:
                            try:
                                if writable:
                                    self.events.emit(frame, d, zone, time.monotonic(), timestamp, alert=alert)
                            except OSError as exc:
                                log.error("event persistence failed (%s)", type(exc).__name__)
                                next_ai = time.monotonic() + 5
                    else:
                        validator.reset()
                        silent_validator.reset()
            # After any event of this frame, so the episode starts with the detection moment.
            self.montage.feed(frame, tick, gate)
            processed += 1
            latency_total += (time.monotonic() - tick) * 1000
            elapsed = time.monotonic() - stats_at
            if elapsed >= self.cfg["logging"]["stats_interval_seconds"]:
                metrics = {
                    "camera_fps": round((self.camera.frame_count - previous_camera_count) / elapsed, 1),
                    "motion_fps": round(motion_count / elapsed, 1),
                    "ai_fps": round(ai_count / elapsed, 2),
                    "processing_latency_ms": round(latency_total / max(1, processed), 1),
                }
                self.status.update(**metrics)
                log.info(
                    "camera FPS=%.1f motion FPS=%.1f AI FPS=%.2f AI inference ms=%.1f processing latency ms=%.1f",
                    metrics["camera_fps"],
                    metrics["motion_fps"],
                    metrics["ai_fps"],
                    inference_total / max(1, ai_count),
                    metrics["processing_latency_ms"],
                )
                stats_at = time.monotonic()
                previous_camera_count = self.camera.frame_count
                motion_count = ai_count = processed = 0
                inference_total = latency_total = 0

    def packet_is_current(self, packet):
        return (
            self.camera.connected
            and self.camera.generation == packet.generation
            and time.monotonic() - packet.captured_at <= self.cfg["camera"]["stale_seconds"]
        )

    def close(self):
        self.status.update(pipeline="stopping")
        self.stop.set()
        self.camera.close()
        self.thread.join(timeout=25)
        notifications_done = self.notifications.close(15) if self.notifications else True
        uploads_done = self.uploader.close(15) if self.uploader else True
        activity_done = self.activity.close(15)
        montage_done = self.montage.close(15)
        if not self.thread.is_alive() and notifications_done and uploads_done and activity_done and montage_done:
            self.store.close()
        else:
            log.warning("shutdown deadline reached; background tasks remain unfinished")
        self.status.update(pipeline="stopped")
        log.info("shutdown complete")
