"""Stream-copy event clips and FFmpeg continuous segment recording.

Only closed media are atomically renamed from .tmp and registered for upload.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import math

log = logging.getLogger(__name__)


def input_args(url, transport):
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-n",
        "-rtsp_transport",
        transport,
        "-timeout",
        "5000000",
        "-i",
        url,
        "-map",
        "0:v:0",
        "-an",
        "-c",
        "copy",
    ]


def stop_process(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def error_summary(handle):
    handle.seek(0, 2)
    length = handle.tell()
    handle.seek(max(0, length - 2000))
    message = handle.read().decode("utf-8", errors="replace").strip()
    return re.sub(r"rtsps?://[^\s\"']+", "<RTSP_URL>", message).replace("\n", " | ")


class EventRecorder:
    def __init__(self, cfg, store, status):
        self.cfg, self.store, self.status = cfg, store, status
        self.process = self.stderr = None
        self.path = self.temporary = None
        self.event_ids = []
        self.started_at = 0
        self.buffer = ContinuousRecorder(cfg, store, buffer=True) if cfg["recording"]["pre_roll_seconds"] else None
        self.parts_dir = None
        self.pre_parts = []

    def poll_buffer(self):
        if self.buffer and self.cfg["recording"]["events"]:
            self.buffer.poll()

    def start_clip(self, path: Path, event_id: str):
        self.poll()
        if self.process:
            self.event_ids.append(event_id)
            return self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.temporary = path, path.with_suffix(".mp4.tmp")
        command = input_args(self.cfg["camera"]["recording_url"], self.cfg["camera"]["transport"])
        command += [
            "-t",
            str(self.cfg["recording"]["clip_seconds"]),
            "-video_track_timescale",
            "90000",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(self.temporary),
        ]
        self.stderr = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.stderr
            )
        except OSError:
            self.stderr.close()
            self.stderr = None
            log.error("recording start failed: ffmpeg unavailable")
            return None
        self.event_ids, self.started_at = [event_id], time.monotonic()
        self.pre_parts = []
        if self.buffer:
            self.buffer._collect_closed()
            files = sorted(self.buffer.directory.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
            recent = [
                p for p in files if time.time() - p.stat().st_mtime < self.cfg["recording"]["pre_roll_seconds"] + 6
            ]
            if recent:
                self.parts_dir = Path(tempfile.mkdtemp(prefix=".preroll-", dir=path.parent))
                for index, source in enumerate(recent[-math.ceil(self.cfg["recording"]["pre_roll_seconds"] / 2) - 1 :]):
                    target = self.parts_dir / f"{index}.part"
                    target.hardlink_to(source)
                    self.pre_parts.append(target)
        self.status.update(recording_status="recording")
        log.info("recording started: %s", path)
        return path

    def _finish(self, code, graceful=False):
        completed = False
        if (code == 0 or graceful and code == 255) and self.temporary.exists():
            try:
                # Check trailer/stream, including interrupted-but-cleanly-closed recordings.
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=codec_name",
                        "-of",
                        "csv=p=0",
                        str(self.temporary),
                    ],
                    capture_output=True,
                    timeout=8,
                )
                if probe.returncode == 0 and probe.stdout.strip():
                    self._prepend()
                    self.temporary.replace(self.path)
                    self.store.register_media(self.path, "event_clip")
                    completed = True
            except (OSError, subprocess.TimeoutExpired):
                pass
        state = "completed" if completed else "failed"
        for event_id in self.event_ids:
            self.store.update(event_id, recording_status=state)
        if not completed:
            log.error("recording stopped: exit=%s %s", code, error_summary(self.stderr))
        else:
            log.info("recording stopped: %s", self.path)
        self.stderr.close()
        self.process = self.stderr = None
        if self.parts_dir:
            # Only this instance's mkdtemp directory, never a broad data path.
            shutil.rmtree(self.parts_dir)
            self.parts_dir = None
        self.pre_parts = []
        self.status.update(recording_status=state)

    def _prepend(self):
        if not self.pre_parts:
            return
        # Concat requires identical time bases. RTSP and segment muxers can
        # otherwise choose 1/90000 vs 1/12288 and stretch the output timeline.
        normalized = []
        for index, source in enumerate([*self.pre_parts, self.temporary]):
            target = self.parts_dir / f"normalized-{index}.part"
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c",
                    "copy",
                    "-video_track_timescale",
                    "90000",
                    "-f",
                    "mp4",
                    str(target),
                ],
                stdout=subprocess.DEVNULL,
                stderr=self.stderr,
                timeout=30,
            )
            if result.returncode:
                raise OSError("Pre-roll normalization failed")
            normalized.append(target)
        manifest = self.parts_dir / "concat.txt"
        manifest.write_text(
            "".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in normalized),
            encoding="utf-8",
        )
        output = self.parts_dir / "joined.tmp"
        joined = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest),
                "-c",
                "copy",
                "-video_track_timescale",
                "90000",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(output),
            ],
            stdout=subprocess.DEVNULL,
            stderr=self.stderr,
            timeout=30,
        )
        if joined.returncode:
            raise OSError("Pre-roll assembly failed")
        output.replace(self.temporary)

    def poll(self):
        if self.process is None:
            return
        code = self.process.poll()
        if code is not None:
            self._finish(code)
        elif time.monotonic() - self.started_at > self.cfg["recording"]["clip_seconds"] + 20:
            log.warning("recording duration watchdog")
            self.close()

    def close(self):
        if self.process:
            stop_process(self.process)
            self._finish(self.process.returncode, graceful=True)
        if self.buffer:
            self.buffer.close()


class ContinuousRecorder:
    def __init__(self, cfg, store, buffer=False):
        self.cfg, self.store = cfg, store
        self.process = self.stderr = None
        self.list_path = None
        self.buffer = buffer
        self.directory = store.root / (".preroll" if buffer else "recordings")
        self.manifest_offset = 0
        self.next_poll = self.next_days = 0
        self.retry_at = 0
        self.failures = 0

    def _prepare_days(self):
        if self.buffer:
            self.directory.mkdir(parents=True, exist_ok=True)
            return
        now = datetime.now().astimezone()
        for day in (now, now + timedelta(days=1)):
            (self.store.root / "recordings" / day.strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)

    def _start(self):
        self._prepare_days()
        if self.buffer:
            # No publisher is active when _start is called. Remove only owned,
            # abandoned ring segments from a previous crash, never event evidence.
            for path in self.directory.glob("*.mp4.tmp"):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9a-f]{8}\.mp4\.tmp", path.name):
                    path.unlink(missing_ok=True)
        session = uuid.uuid4().hex[:8]
        manifests = self.directory / ".manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        self.list_path = manifests / f"{session}.csv"
        self.manifest_offset = 0
        pattern = self.directory / (
            f"%Y-%m-%d_%H-%M-%S_{session}.mp4.tmp"
            if self.buffer
            else "%Y-%m-%d/" + f"%Y-%m-%d_%H-%M-%S_{session}.mp4.tmp"
        )
        command = input_args(self.cfg["camera"]["recording_url"], self.cfg["camera"]["transport"])
        command += [
            "-f",
            "segment",
            "-segment_time",
            "2" if self.buffer else str(self.cfg["recording"]["segment_seconds"]),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            "-segment_format",
            "mp4",
            "-segment_format_options",
            "movflags=+faststart:video_track_timescale=90000",
            "-segment_list_type",
            "csv",
            "-segment_list",
            str(self.list_path),
            str(pattern),
        ]
        self.stderr = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.stderr
            )
            log.info("continuous recording started")
        except OSError:
            self.stderr.close()
            self.stderr = None
            self.retry_at = time.monotonic() + 30
            log.error("continuous recording start failed: ffmpeg unavailable")

    def _collect_closed(self):
        if not self.list_path or not self.list_path.exists():
            return
        # FFmpeg lists a segment only after closing its muxer/trailer.
        with self.list_path.open(encoding="utf-8") as handle:
            handle.seek(self.manifest_offset)
            while True:
                line = handle.readline()
                if not line or not line.endswith("\n"):
                    break
                self.manifest_offset = handle.tell()
                row = next(csv.reader([line]))
                if len(row) != 3:
                    continue
                temporary = Path(row[0])
                if not temporary.is_absolute():
                    # Segment CSV may contain only basename, even for absolute output templates.
                    temporary = (
                        self.directory / temporary.name
                        if self.buffer
                        else self.directory / temporary.name[:10] / temporary.name
                    )
                try:
                    temporary.resolve().relative_to(self.directory)
                except ValueError:
                    continue
                if temporary.name.endswith(".mp4.tmp") and temporary.exists():
                    final = temporary.with_suffix("")
                    temporary.replace(final)
                    if not self.buffer:
                        self.store.register_media(final, "continuous")
                        log.info("recording segment closed: %s", final)
                    self.failures = 0

    def poll(self):
        if not (self.buffer or self.cfg["recording"]["continuous"]):
            return
        now = time.monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + 1
        if now >= self.next_days:
            self._prepare_days()
            self.next_days = now + 3600
        self._collect_closed()
        if self.buffer:
            for path in self.directory.glob("*.mp4"):
                if time.time() - path.stat().st_mtime > self.cfg["recording"]["pre_roll_seconds"] + 10:
                    path.unlink(missing_ok=True)
        # Rotate the process/manifest daily; bounded manifest even over years of uptime.
        if self.process is not None and self.list_path.exists() and self.list_path.stat().st_size > 4_000_000:
            self.close()
        if self.process is not None and self.process.poll() is not None:
            self._collect_closed()
            log.warning("continuous recording stopped: exit=%s %s", self.process.returncode, error_summary(self.stderr))
            self.stderr.close()
            self.process = self.stderr = None
            self.list_path.unlink(missing_ok=True)
            self.failures += 1
            self.retry_at = time.monotonic() + min(30, 2 ** min(self.failures - 1, 5))
        if self.process is None and time.monotonic() >= self.retry_at:
            self._start()

    def close(self):
        if self.process:
            stop_process(self.process)
            self._collect_closed()
            self.stderr.close()
            self.process = self.stderr = None
            if self.list_path:
                self.list_path.unlink(missing_ok=True)
            log.info("continuous recording stopped (shutdown)")
