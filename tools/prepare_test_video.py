"""Append a frozen last frame efficiently; no hour-long re-encoding required."""

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def prepare(source: Path, output: Path, seconds: int = 3600):
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise ValueError("Input video does not exist")
    if output.exists() or source == output:
        raise ValueError("Output already exists; choose a new path")
    if seconds < 1:
        raise ValueError("Hold duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="camera-test-") as directory:
        work = Path(directory)
        original, frame, hold = work / "original.mp4", work / "last.png", work / "hold.mp4"
        # Identical video parameters make stream-copy concatenation reliable.
        encoding = [
            "-an",
            "-vf",
            "fps=24,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-profile:v",
            "baseline",
            "-g",
            "24",
            "-keyint_min",
            "24",
            "-sc_threshold",
            "0",
            "-video_track_timescale",
            "12288",
        ]
        run("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", source, *encoding, original)
        # The normalized stream's actual final decoded frame, not an approximate seek.
        run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-sseof",
            "-1",
            "-i",
            original,
            "-vf",
            "reverse",
            "-frames:v",
            "1",
            frame,
        )
        chunk = min(10, seconds)
        run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-loop",
            "1",
            "-i",
            frame,
            "-t",
            chunk,
            *encoding,
            hold,
        )
        parts = [f"file '{original.as_posix()}'"] + [f"file '{hold.as_posix()}'"] * math.ceil(seconds / chunk)
        # Exact requested hold length, even when not divisible by chunk.
        metadata = json.loads(
            subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(original)]
            )
        )
        total = float(metadata["format"]["duration"]) + seconds
        manifest = work / "concat.txt"
        manifest.write_text("\n".join(parts) + "\n", encoding="utf-8")
        temporary = output.with_suffix(".mp4.tmp")
        try:
            run(
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
                manifest,
                "-t",
                total,
                "-c:v",
                "copy",
                "-an",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                temporary,
            )
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Created {output}: original video + {seconds}s frozen final frame (video only)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--hold-seconds", type=int, default=3600)
    args = parser.parse_args()
    prepare(args.source, args.output, args.hold_seconds)


if __name__ == "__main__":
    main()
