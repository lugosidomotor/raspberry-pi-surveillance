"""Host recovery watchdog for the camera stack (root, via systemd). Python 3.9+ standard library only.

A hung VideoCore firmware takes the camera, ISP and codecs down with it, and only a reboot clears it.
This service reboots the Pi when the kernel reports firmware transaction timeouts, and recovers a camera
that stays down: first by restarting the camera-source user service, then by rebooting. Reboots are
rate limited and can be switched off from the web UI; systemd's hardware watchdog (RebootWatchdogSec)
forces the reset if the reboot itself hangs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import pwd
import signal
import subprocess
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("camera-watchdog")
STOP = threading.Event()
FIRMWARE_MARK = "Firmware transaction timeout"
POLICY = {"interval": 30, "boot_grace": 300, "camera_down": 180, "reboot_gap": 1800, "max_reboots": 3}
DAY = 86400


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds") if timestamp else None


def decide(memory: dict, observation: dict, now: float, policy: dict, auto_reboot: bool):
    """Pure policy. Returns (action, state, message); ``memory`` is updated in place.

    action: None, "restart-source" or "reboot". memory keys: camera_down_since, source_restarted_at,
    source_restart_failed (the restart itself hung: the camera is stuck in the kernel), reboots.
    """
    want_reboot = None
    if observation["firmware_hang"]:
        want_reboot = "A GPU-firmware nem válaszol (firmware transaction timeout)."
    elif observation["camera_ok"]:
        memory["camera_down_since"] = memory["source_restarted_at"] = None
        memory["source_restart_failed"] = False
        return None, "ok", "Minden rendben."
    elif observation["uptime"] < policy["boot_grace"]:
        return None, "warning", "Nincs kép; indulás utáni türelmi idő."
    else:
        memory["camera_down_since"] = memory.get("camera_down_since") or now
        down_for = now - memory["camera_down_since"]
        restarted = memory.get("source_restarted_at")
        if restarted is None:
            if down_for < policy["camera_down"]:
                return None, "warning", f"Nincs kép {int(down_for)} mp óta; figyelés."
            memory["source_restarted_at"] = now
            return "restart-source", "recovering", "Nincs kép 3 perce: a kameraforrás újraindítása."
        if memory.get("source_restart_failed"):
            want_reboot = "A kameraforrás nem indítható újra (a kamera a kernelben ragadt)."
        elif now - restarted < policy["camera_down"]:
            return None, "recovering", "A kameraforrás újraindítva; várakozás a képre."
        else:
            want_reboot = "A kameraforrás újraindítása után sincs kép."
    if not auto_reboot:
        return None, "blocked", want_reboot + " Az automatikus újraindítás ki van kapcsolva."
    recent = [t for t in memory.get("reboots", []) if now - t < DAY]
    if recent and now - max(recent) < policy["reboot_gap"]:
        return None, "blocked", want_reboot + " Az előző újraindítás túl friss (30 perces korlát)."
    if len(recent) >= policy["max_reboots"]:
        return None, "blocked", want_reboot + f" Már {len(recent)} újraindítás volt 24 órán belül."
    memory["reboots"] = recent + [now]
    return "reboot", "rebooting", want_reboot + " Újraindítás."


def observe(status_url: str, policy: dict) -> dict:
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = float(policy["boot_grace"])
    try:
        kernel_log = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=10).stdout
        firmware_hang = FIRMWARE_MARK in kernel_log
    except (OSError, subprocess.SubprocessError):
        firmware_hang = False
    try:
        with urllib.request.urlopen(status_url, timeout=5) as response:
            camera_ok = json.load(response).get("camera") == "connected"
    except (OSError, ValueError):
        camera_ok = False  # an unreachable app is a camera that is down as far as recovery is concerned
    return {"uptime": uptime, "firmware_hang": firmware_hang, "camera_ok": camera_ok}


def auto_reboot_enabled(settings_file: Path) -> bool:
    try:
        value = json.loads(settings_file.read_text()).get("watchdog_auto_reboot", True)
    except (OSError, ValueError, AttributeError):
        return True
    return value if isinstance(value, bool) else True


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)  # the non-root app container reads it
    temporary.replace(path)


DIAGNOSTIC_COMMANDS = [
    ["uptime"],
    ["sh", "-c", "dmesg | tail -n 150"],
    [
        "sh",
        "-c",
        "ps -eo pid,stat,etime,pcpu,wchan:24,args | grep -E 'libcamera|rpicam|ffmpeg|source.py|PID' | grep -v grep",
    ],
    [
        "sh",
        "-c",
        "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; cat /sys/devices/system/cpu/cpu0/cpufreq/stats/total_trans",
    ],
    [
        "sh",
        "-c",
        "grep -E 'Cma|MemAvailable' /proc/meminfo; cat /sys/class/thermal/thermal_zone0/temp; cat /sys/class/hwmon/hwmon*/in0_lcrit_alarm",
    ],
    ["timeout", "3", "vcgencmd", "get_throttled"],
    ["journalctl", "_SYSTEMD_USER_UNIT=camera-source.service", "-b", "-n", "40", "--no-pager", "-o", "short-precise"],
]


def save_diagnostics(folder: Path, action: str, now: float, keep: int = 30) -> None:
    """Snapshot the evidence a recovery destroys (kernel log, process states) before acting on it."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for command in DIAGNOSTIC_COMMANDS:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = (result.stdout + result.stderr).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            output = f"<{type(exc).__name__}>"
        lines.append(f"$ {' '.join(command)}\n{output}\n")
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}-{action}.txt"
    path.write_text("\n".join(lines))
    os.chmod(path, 0o644)
    for old in sorted(folder.glob("*.txt"))[:-keep]:
        old.unlink(missing_ok=True)


def act(action: str, owner: str, dry_run: bool) -> str:
    if dry_run:
        LOG.warning("dry run: would %s", action)
        return "dry-run"
    if action == "restart-source":
        command = ["systemctl", f"--machine={owner}@.host", "--user", "restart", "camera-source"]
    else:
        # Skip the orderly unit stop: it only waits minutes on processes stuck in the camera driver.
        # Processes are still killed and file systems unmounted; the hardware watchdog backs this up.
        command = ["systemctl", "reboot", "--force"]
    try:
        # A camera process stuck in the kernel can make the restart hang; the timeout escalates later.
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        return "ok" if result.returncode == 0 else f"exit {result.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:
        return type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--status-url", default="http://127.0.0.1:8000/api/status")
    parser.add_argument("--dry-run", action="store_true", help="log decisions without restarting anything")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    status_file, settings_file = args.data_dir / "watchdog-status.json", args.data_dir / "settings.json"
    owner = pwd.getpwuid(ROOT.stat().st_uid).pw_name
    try:
        previous = json.loads(status_file.read_text())
    except (OSError, ValueError):
        previous = {}
    memory = {
        "camera_down_since": None,
        "source_restarted_at": None,
        "source_restart_failed": False,
        "reboots": [t for t in previous.get("reboot_times", []) if isinstance(t, (int, float))],
    }
    history = previous.get("history", [])[-10:] if isinstance(previous.get("history"), list) else []
    LOG.info("watching (dry run: %s, owner: %s)", args.dry_run, owner)
    while not STOP.is_set():
        observation = observe(args.status_url, POLICY)
        auto_reboot = auto_reboot_enabled(settings_file)
        now = now_ts()
        action, state, message = decide(memory, observation, now, POLICY, auto_reboot)
        if action:
            LOG.warning("%s: %s", action, message)
            history = (history + [{"at": iso(now), "action": action, "reason": message}])[-10:]
        status = {
            "updated_at": iso(now),
            "interval": POLICY["interval"],
            "state": state,
            "message": message,
            "firmware_ok": not observation["firmware_hang"],
            "camera_ok": observation["camera_ok"],
            "auto_reboot": auto_reboot,
            "camera_down_since": iso(memory["camera_down_since"]),
            "reboots_24h": len([t for t in memory["reboots"] if now - t < DAY]),
            "reboot_times": memory["reboots"],
            "history": history,
            "dry_run": args.dry_run,
        }
        try:
            write_json(status_file, status)  # before acting, so a reboot is recorded
        except OSError as exc:
            LOG.warning("status not written (%s)", type(exc).__name__)
        if action:
            try:
                save_diagnostics(args.data_dir / "diagnostics", action, now)
            except OSError as exc:
                LOG.warning("diagnostics not saved (%s)", type(exc).__name__)
            result = act(action, owner, args.dry_run)
            LOG.warning("%s result: %s", action, result)
            history[-1]["result"] = result
            if action == "restart-source" and result not in ("ok", "dry-run"):
                memory["source_restart_failed"] = True  # escalate at the next check instead of waiting
        STOP.wait(POLICY["interval"])


if __name__ == "__main__":
    main()
