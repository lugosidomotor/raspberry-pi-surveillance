"""One RTSP reader thread, bounded read timeouts and a single latest-frame slot."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)
# Clockwise degrees, the same direction as CSS rotate() in the UI.
ROTATE_CODES = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass(frozen=True)
class CameraFrame:
    image: np.ndarray
    sequence: int
    generation: int
    captured_at: float


class RTSPCamera:
    def __init__(self, cfg: dict, rotation=lambda: 0):
        self.cfg = cfg
        self.rotation = rotation  # read per frame, so a UI change applies to the very next frame
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest: CameraFrame | None = None
        self.frame_count = 0
        self._generation = 0
        self._connected = False
        self._thread = threading.Thread(target=self._run, name="rtsp-reader", daemon=True)

    @property
    def connected(self) -> bool:
        with self._condition:
            return self._connected

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def start(self) -> None:
        self._thread.start()

    def reconnect_delay(self, failures: int) -> float:
        delays = (1, 2, 5, 10, 20, 30)
        return min(delays[min(failures, len(delays) - 1)], self.cfg["reconnect_max_seconds"])

    def read(self, after_sequence: int, timeout: float = 0.1) -> CameraFrame | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._stop.is_set() or (self._latest is not None and self._latest.sequence > after_sequence),
                timeout,
            )
            packet = self._latest
            if packet is None or packet.sequence <= after_sequence:
                return None
            if time.monotonic() - packet.captured_at > self.cfg["stale_seconds"]:
                return None
            return packet

    def _open(self, decoder: str, params: list) -> cv2.VideoCapture:
        # FFmpeg reads this setting at open time. This prototype has one camera.
        options = f"rtsp_transport;{self.cfg['transport']}"
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = options + (f"|video_codec;{decoder}" if decoder else "")
        return cv2.VideoCapture(self.cfg["url"], cv2.CAP_FFMPEG, params)

    def _run(self) -> None:
        decoder = self.cfg.get("decoder", "")
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            int(self.cfg["open_timeout_seconds"] * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            int(self.cfg["read_timeout_seconds"] * 1000),
        ]
        failures = 0
        while not self._stop.is_set():
            cap = None
            announced = False
            applied_rotation = None
            try:
                cap = self._open(decoder, params)
                if decoder and not cap.isOpened():
                    # A missing or busy hardware decoder must never cost the stream. Software decoding is kept
                    # only if it opens; otherwise the stream itself is down and the hardware choice stays.
                    cap.release()
                    cap = self._open("", params)
                    if cap.isOpened():
                        log.warning("hardware decoder %s unavailable; using software decoding", decoder)
                        decoder = ""
                while cap.isOpened() and not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None or not frame.size:
                        break
                    rotation = self.rotation()
                    if rotation in ROTATE_CODES:
                        frame = cv2.rotate(frame, ROTATE_CODES[rotation])
                    with self._condition:
                        if announced and rotation != applied_rotation:
                            # A new generation resets motion background and votes for the turned picture.
                            self._generation += 1
                        applied_rotation = rotation
                        if not announced:
                            self._generation += 1
                            self._connected = True
                            announced = True
                            failures = 0
                            log.info(
                                "stream connected (generation %d, decoder %s)", self._generation, decoder or "software"
                            )
                        self.frame_count += 1
                        self._latest = CameraFrame(frame, self.frame_count, self._generation, time.monotonic())
                        self._condition.notify_all()
            except Exception as exc:
                # Never put an exception message containing the URL/token into app logs.
                log.warning("stream error (%s)", type(exc).__name__)
            finally:
                if cap is not None:
                    cap.release()
                with self._condition:
                    self._connected = False
                    self._latest = None
                    self._condition.notify_all()
            if self._stop.is_set():
                break
            if announced:
                log.warning("stream disconnected")
            delay = self.reconnect_delay(failures)
            failures += 1
            log.warning("reconnecting in %.1f sec (attempt %d)", delay, failures)
            self._stop.wait(delay)

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        # Only the reader owns/releases VideoCapture; avoid cross-thread native races.
        self._thread.join(timeout=max(self.cfg["open_timeout_seconds"], self.cfg["read_timeout_seconds"]) + 2)
        if self._thread.is_alive():
            log.warning("camera reader did not exit within configured timeout")
