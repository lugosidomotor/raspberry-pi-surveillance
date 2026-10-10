"""Compose-managed, infinite test-video publisher; no camera hardware access."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

log = logging.getLogger("publisher")


def write_status(path, mode, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"mode": mode, "state": state, "updated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    temporary.replace(path)


def test_command(video, url):
    if not Path(video).is_file():
        raise ValueError("Missing test video: prepare data/test/source.mp4 first")
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        url,
    ]


def main():
    if "--health" in sys.argv[1:]:
        # Also supports the CSI one-off container, whose command is plain FFmpeg.
        for path in Path("/proc").glob("[0-9]*/comm"):
            try:
                if path.read_text().strip() == "ffmpeg":
                    return
            except OSError:
                continue
        raise SystemExit(1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    status = Path(os.getenv("DATA_DIR", "/data")) / "source-status.json"
    try:
        command = test_command(
            os.getenv("SOURCE_VIDEO", "/data/test/source.mp4"),
            os.getenv("SOURCE_RTSP_URL", "rtsp://mediamtx:8554/cam"),
        )
        while not stop.is_set():
            process = subprocess.Popen(command, stderr=subprocess.DEVNULL)
            write_status(status, "test", "publishing")
            log.info("Test stream publishing (infinite loop)")
            try:
                while not stop.wait(1):
                    if process.poll() is not None:
                        log.warning("Publisher disconnected; retrying in 2 seconds")
                        break
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                write_status(status, "test", "stopped")
            stop.wait(2)
    except (ValueError, OSError) as exc:
        log.error("Publisher startup failed: %s", type(exc).__name__)
        write_status(status, "test", "stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
