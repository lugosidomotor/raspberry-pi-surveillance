"""Restart-persistent rclone upload ledger, exclusively finalized media."""

import logging
import subprocess
import threading
import time
import tempfile
from .recorder import error_summary

log = logging.getLogger(__name__)


class RcloneUploader:
    def __init__(self, cfg, store, status):
        self.cfg, self.store, self.status = cfg, store, status
        self.stop = threading.Event()
        self.process_lock = threading.Lock()
        self.active_process = None
        self.thread = threading.Thread(target=self._run, name="rclone-uploader", daemon=True)

    def start(self):
        self.thread.start()

    def scan(self):
        # Atomic final names and the recorder closure manifest are the commit boundary.
        for directory in (self.store.root / "events", self.store.root / "recordings"):
            if directory.exists():
                for path in directory.rglob("*"):
                    if (
                        path.is_file()
                        and not path.name.endswith("_thumb.jpg")
                        and path.suffix.lower() in (".jpg", ".mp4", ".mkv")
                    ):
                        try:
                            kind = (
                                "snapshot"
                                if path.suffix == ".jpg"
                                else "continuous"
                                if directory.name == "recordings"
                                else "event_clip"
                            )
                            self.store.register_media(path, kind)
                        except (ValueError, OSError):
                            continue

    def upload(self, entry):
        relative = entry["path"]
        path = (self.store.root / relative).resolve()
        try:
            path.relative_to(self.store.root)
        except ValueError:
            return False
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".mp4", ".mkv"):
            self.store.missing_media(relative)
            return False
        if not self.store.claim_media(relative):
            return False
        destination = self.cfg["remote"] + self.cfg["path"].rstrip("/") + "/" + relative
        self.status.update(upload_status="uploading", upload_last_file=relative)
        log.info("backup upload started: %s", relative)
        stderr = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(
                [
                    "rclone",
                    "copyto",
                    str(path),
                    destination,
                    "--retries",
                    "1",
                    "--low-level-retries",
                    "1",
                    "--contimeout",
                    "10s",
                    "--timeout",
                    "30s",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
            with self.process_lock:
                self.active_process = process
            code = process.wait(timeout=self.cfg["timeout_seconds"])
            if code:
                raise OSError("rclone returned nonzero")
            self.store.upload_result(relative, True)
            # Snapshots stay available for UI and pending notifications. Only videos are deleted.
            if self.cfg["delete_after_upload"] and path.suffix.lower() in (".mp4", ".mkv"):
                path.unlink()
                log.info("local video deleted after successful upload: %s", relative)
            self.status.update(upload_status="finished")
            log.info("backup upload finished: %s", relative)
            return True
        except (OSError, subprocess.TimeoutExpired) as exc:
            delay = min(3600, 30 * 2 ** min(entry["attempts"], 7))
            self.store.upload_result(relative, False, time.time() + delay)
            self.status.update(upload_status="failed")
            detail = error_summary(stderr)
            # rclone can echo remote credentials; log only known error categories.
            category = next(
                (
                    word
                    for word in ("permission denied", "connection refused", "timeout", "not found", "quota")
                    if word in detail.lower()
                ),
                "see rclone connectivity/configuration",
            )
            log.warning(
                "backup upload failed: %s (%s: %s); retry in %ss", relative, type(exc).__name__, category, delay
            )
            return False
        finally:
            with self.process_lock:
                active = self.active_process
                if active is not None and active.poll() is None:
                    active.kill()
                    active.wait(timeout=3)
                self.active_process = None
            stderr.close()

    def _run(self):
        try:
            self.scan()  # Startup-only reconciliation; recorders register new files directly.
        except Exception as exc:
            log.error("backup reconciliation failed (%s)", type(exc).__name__)
        while not self.stop.is_set():
            try:
                for entry in self.store.pending_media(time.time()):
                    if self.stop.is_set():
                        break
                    self.upload(entry)
            except Exception as exc:
                self.status.update(upload_status="failed")
                log.error("backup worker error (%s)", type(exc).__name__)
            self.stop.wait(self.cfg["scan_seconds"])

    def close(self, timeout=15):
        self.stop.set()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            with self.process_lock:
                if self.active_process is not None and self.active_process.poll() is None:
                    self.active_process.terminate()
            self.thread.join(timeout=5)
        return not self.thread.is_alive()
