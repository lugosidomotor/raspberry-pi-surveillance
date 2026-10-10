"""Host-only CSI capture adapter for the shared camera-stack Compose project.

Python 3.9+ standard library only. FFmpeg runs in the existing application image;
the CSI capture tool runs on the host, never inside the AI container.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
# The encoder default (~9-10 Mbit/s at 720p) saturates a Pi 4 once MediaMTX, the AI app
# and a Tailscale viewer all handle it; 2 Mbit/s keeps 720p surveillance detail.
BITRATE = 2_000_000
# Detection samples at most 8 FPS (motion) and 2-3 FPS (AI); every extra frame is pure decode cost.
FRAMERATE = 12
# libcamera's own pick for 1280x720 is a centre 1920x1080 crop of the IMX219 (~39x22 deg). The full-sensor
# binned mode (CAMERA_SIZE=1280x960, CAMERA_SENSOR_MODE=1640:1232:10:P, ~62x49 deg) wedged the camera
# pipeline twice within minutes on firmware 1.20230405 / libcamera 0.0.5, so it is opt-in only.
DEFAULT_SIZE = "1280x720"
DEFAULT_SENSOR_MODE = ""
# No camera bytes, or no publisher progress, for this long means the pipeline is stuck.
STALL_SECONDS = 10
HEARTBEAT_SECONDS = 300  # periodic pipeline state in the journal, for diagnosing silent stalls
PUBLISHER = "camera-stack-publisher-csi"
STOP = threading.Event()
LOG = logging.getLogger("camera-source")


def read_env(path):
    """KEY=VALUE pairs from the stack's .env (the host adapter shares it with Docker Compose)."""
    values = {}
    try:
        for line in path.read_text().splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and not key.startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


TUNING_DIRS = (Path("/usr/share/libcamera/ipa/rpi/vc4"), Path("/usr/share/libcamera/ipa/raspberrypi"))


def capture_settings(env):
    """Capture size, sensor mode and optional tuning file from .env, falling back on invalid values."""
    size = env.get("CAMERA_SIZE") or DEFAULT_SIZE
    if not re.fullmatch(r"[1-9][0-9]{2,3}x[1-9][0-9]{2,3}", size):
        LOG.warning("invalid CAMERA_SIZE %r; using %s", size, DEFAULT_SIZE)
        size = DEFAULT_SIZE
    mode = env.get("CAMERA_SENSOR_MODE", DEFAULT_SENSOR_MODE)
    if mode and not re.fullmatch(r"[0-9]{3,4}:[0-9]{3,4}(:(8|10|12)(:[PU])?)?", mode):
        LOG.warning("invalid CAMERA_SENSOR_MODE %r; letting libcamera choose", mode)
        mode = DEFAULT_SENSOR_MODE
    # A NoIR module (no infrared filter) needs e.g. CAMERA_TUNING_FILE=imx219_noir for sane colours.
    tuning = env.get("CAMERA_TUNING_FILE", "")
    if tuning:
        candidates = [Path(tuning)] + [folder / f"{tuning}.json" for folder in TUNING_DIRS]
        tuning = next((str(path) for path in candidates if path.is_file()), "")
        if not tuning:
            LOG.warning("CAMERA_TUNING_FILE %r not found; using the libcamera default", env["CAMERA_TUNING_FILE"])
    # software: x264 on the CPU (~half a core at 720p/12 FPS) instead of the VideoCore H.264 encoder,
    # whose firmware path stalled in every camera hang of 2026-10-10. The ISP still runs on the GPU.
    encoder = env.get("CAMERA_ENCODER") or "hardware"
    if encoder not in ("hardware", "software"):
        LOG.warning("invalid CAMERA_ENCODER %r; using hardware", encoder)
        encoder = "hardware"
    width, height = size.split("x")
    return {"width": width, "height": height, "mode": mode, "tuning": tuning, "encoder": encoder}


def camera_tool():
    for name in ("rpicam-vid", "libcamera-vid"):
        binary = shutil.which(name)
        if not binary:
            continue
        try:
            probe = subprocess.run([binary, "--list-cameras"], capture_output=True, text=True, timeout=15)
            if probe.returncode == 0 and re.search(r"(?m)^\s*\d+\s*:", probe.stdout + probe.stderr):
                return binary
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def write_status(path, mode, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"mode": mode, "state": state, "updated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    temporary.replace(path)


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class Relay:
    """Drains the camera pipe continuously and feeds whichever publisher is attached.

    The camera must never block on a full pipe: a hardware H.264 encoder left with undrained buffers
    (e.g. behind a publisher whose RTSP session died) can wedge the kernel camera driver until reboot.
    When the publisher falls behind, chunks are dropped instead; the stream resyncs at the next keyframe.
    """

    def __init__(self, source, chunk=16384, depth=64):
        self.source, self.chunk = source, chunk
        self.chunks = queue.Queue(maxsize=depth)
        self.read_at = self.written_at = time.monotonic()
        self.dropped = 0
        self.bytes_read = 0
        self.ended = threading.Event()

    def start(self):
        threading.Thread(target=self._read, name="camera-reader", daemon=True).start()

    def _read(self):
        while True:
            try:
                data = os.read(self.source.fileno(), self.chunk)
            except OSError:
                data = b""
            if not data:
                self.ended.set()
                return
            self.read_at = time.monotonic()
            self.bytes_read += len(data)
            try:
                self.chunks.put_nowait(data)
            except queue.Full:
                self.dropped += 1

    def attach(self, sink):
        """Feed ``sink`` from now on; stale queued data is discarded so the new publisher starts live."""
        while not self.chunks.empty():
            try:
                self.chunks.get_nowait()
            except queue.Empty:
                break
        self.written_at = time.monotonic()
        threading.Thread(target=self._write, args=(sink,), name="publisher-writer", daemon=True).start()

    def _write(self, sink):
        while True:
            try:
                data = self.chunks.get(timeout=1)
            except queue.Empty:
                continue
            try:
                sink.write(data)
                sink.flush()
            except (OSError, ValueError):
                return  # publisher gone: the monitor starts a new one
            self.written_at = time.monotonic()


def capture_command(binary, settings=None):
    settings = settings or capture_settings({})
    if settings.get("encoder") == "software":
        # Raw ISP output; encoder_command() turns it into H.264 on the CPU (libcamera-apps 1.2's libav mode
        # cannot set x264 options and its default adds B-frames and ~3 s of lookahead).
        codec = ["--codec", "yuv420"]
    else:
        codec = ["--codec", "h264"]
    command = [binary, "--timeout", "0", "--nopreview", "--inline", *codec, "--width", settings["width"]]
    command += ["--mode", settings["mode"]] if settings["mode"] else []
    command += ["--tuning-file", settings["tuning"]] if settings["tuning"] else []
    return command + [
        "--height",
        settings["height"],
        "--framerate",
        str(FRAMERATE),
        "--intra",
        str(FRAMERATE),
        "--bitrate",
        str(BITRATE),
        "--output",
        "-",
    ]


def encoder_command(settings):
    """Host FFmpeg x264 for the software encoder: no B-frames, no lookahead, one keyframe per second."""
    if settings.get("encoder") != "software":
        return None
    size = f"{settings['width']}x{settings['height']}"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", size] + [
        "-r",
        str(FRAMERATE),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-g",
        str(FRAMERATE),
        "-b:v",
        str(BITRATE),
        "-maxrate",
        str(BITRATE),
        "-bufsize",
        str(BITRATE // 2),
        "-f",
        "h264",
        "-",
    ]


def publisher_command(rtsp_url):
    # Compose owns the FFmpeg helper, image, mounts and bridge networking.
    # -T disables the pseudo-TTY, NOT stdin; --interactive keeps the H.264 pipe open.
    helper = ["docker", "compose", "--project-directory", str(ROOT), "run", "--rm", "--no-deps", "--interactive"]
    helper += ["-T", "--name", PUBLISHER, "publisher", "ffmpeg"]
    return helper + [
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-f",
        "h264",
        "-r",
        str(FRAMERATE),
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]


def remove_publisher():
    # docker CLI termination alone may leave its attached container running.
    subprocess.run(["docker", "rm", "-f", PUBLISHER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def publish(args, binary):
    if not binary:
        raise ValueError("CSI camera unavailable; use the test-source Compose profile for test video")
    capture = encoder = publisher = None
    remove_publisher()  # a leftover one-off from a crash would block the container name
    try:
        settings = capture_settings(read_env(ROOT / ".env"))
        LOG.info(
            "capture %sx%s, sensor mode %s, tuning %s, %s H.264 encoder",
            settings["width"],
            settings["height"],
            settings["mode"] or "auto",
            settings["tuning"] or "default",
            settings["encoder"],
        )
        capture = subprocess.Popen(capture_command(binary, settings), stdout=subprocess.PIPE)
        output = capture.stdout
        if encoder_command(settings):
            if not shutil.which("ffmpeg"):
                raise ValueError("CAMERA_ENCODER=software needs ffmpeg with libx264 on the host")
            encoder = subprocess.Popen(encoder_command(settings), stdin=capture.stdout, stdout=subprocess.PIPE)
            capture.stdout.close()  # the encoder owns the raw pipe now
            output = encoder.stdout
        relay = Relay(output)
        relay.start()
        while not STOP.is_set():
            LOG.info("CSI camera publishing through Compose")
            publisher = subprocess.Popen(publisher_command(args.rtsp_url), stdin=subprocess.PIPE)
            relay.attach(publisher.stdin)
            write_status(args.status_file, "camera", "publishing")
            heartbeat = time.monotonic()
            while not STOP.wait(1):
                now = time.monotonic()
                if now - heartbeat >= HEARTBEAT_SECONDS:
                    heartbeat = now
                    LOG.info(
                        "pipeline ok: %.1f MiB from camera, last data %.1fs ago, %d chunks dropped",
                        relay.bytes_read / 2**20,
                        now - relay.read_at,
                        relay.dropped,
                    )
                if capture.poll() is not None or relay.ended.is_set() or (encoder and encoder.poll() is not None):
                    raise RuntimeError("Camera capture stopped; retrying source selection")
                if now - relay.read_at > STALL_SECONDS:
                    raise RuntimeError("Camera output stalled; restarting the capture")
                if publisher.poll() is not None or now - relay.written_at > STALL_SECONDS:
                    # Only the publisher is replaced; the camera keeps streaming into the relay meanwhile.
                    LOG.warning(
                        "publisher %s (exit %s, %d chunks dropped); restarting it",
                        "stopped" if publisher.poll() is not None else "stalled",
                        publisher.poll(),
                        relay.dropped,
                    )
                    break
            remove_publisher()
            stop_process(publisher)
    finally:
        remove_publisher()
        stop_process(publisher)
        stop_process(capture)
        stop_process(encoder)
        write_status(args.status_file, "camera", "stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path, default=ROOT / "data/source-status.json")
    parser.add_argument("--rtsp-url", default="rtsp://mediamtx:8554/cam")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    waiting = False
    while not STOP.is_set():
        try:
            binary = camera_tool()
            if not binary:
                if not waiting:
                    # Replace a stale test-source record: a missing camera must not be shown as test video.
                    write_status(args.status_file, "camera", "unavailable")
                    waiting = True
                raise ValueError("CSI camera not available")
            waiting = False
            publish(args, binary)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            LOG.warning("%s", exc)
        STOP.wait(5)


if __name__ == "__main__":
    main()
