import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/pi/install-host.sh"


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def test_script_is_executable_and_valid_bash():
    assert os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_help_and_unknown_options():
    help_text = run("--help")
    assert help_text.returncode == 0
    for option in ("--check", "--skip-camera-source", "--skip-watchdog", "--skip-network", "--skip-cpu-governor"):
        assert option in help_text.stdout
    assert run("--nope").returncode == 2


def test_every_installed_file_ships_with_the_repository():
    text = SCRIPT.read_text()
    for name in set(re.findall(r"\$DEPLOY/([\w.-]+)", text)):
        assert (ROOT / "deploy/pi" / name).is_file(), name
    # Placeholders the script renders must exist in the unit files.
    assert "@ROOT@" in (ROOT / "deploy/pi/camera-watchdog.service").read_text()
    assert "%h/camera-stack" in (ROOT / "deploy/pi/camera-source.service").read_text()


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_clean():
    assert subprocess.run(["shellcheck", "-S", "style", str(SCRIPT)]).returncode == 0
