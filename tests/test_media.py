"""Exercise features required from the deliberately small FFmpeg build."""

import json
import shutil
import subprocess

import pytest

from tools.prepare_test_video import prepare


def test_test_video_preparation_with_runtime_ffmpeg(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    original, output = tmp_path / "input.mp4", tmp_path / "loop.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x120:r=24",
            "-t",
            "2",
            "-c:v",
            "libx264",
            str(original),
        ],
        check=True,
        timeout=15,
    )
    prepare(original, output, seconds=1)
    metadata = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name",
                "-of",
                "json",
                str(output),
            ],
            timeout=8,
        )
    )
    assert 2.9 <= float(metadata["format"]["duration"]) <= 3.1
    assert metadata["streams"][0]["codec_name"] == "h264"
    assert not output.with_suffix(".mp4.tmp").exists()
