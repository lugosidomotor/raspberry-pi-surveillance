from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.events import EventStore
from app.settings import RuntimeSettings
from app.utils import Status
from app.web import create_app
from deploy.pi import watchdog
from deploy.pi.watchdog import POLICY, decide

T0 = 1_000_000.0


def memory():
    return {"camera_down_since": None, "source_restarted_at": None, "reboots": []}


def seen(camera_ok=True, firmware_hang=False, uptime=3600):
    return {"camera_ok": camera_ok, "firmware_hang": firmware_hang, "uptime": uptime}


def test_healthy_system_needs_no_action_and_resets_an_episode():
    m = memory() | {"camera_down_since": T0 - 500, "source_restarted_at": T0 - 100}
    assert decide(m, seen(), T0, POLICY, True) == (None, "ok", "Minden rendben.")
    assert m["camera_down_since"] is None and m["source_restarted_at"] is None


def test_camera_down_escalates_from_source_restart_to_reboot():
    m = memory()
    assert decide(m, seen(camera_ok=False), T0, POLICY, True)[0] is None  # just noticed
    assert decide(m, seen(camera_ok=False), T0 + 179, POLICY, True)[1] == "warning"
    action, state, _ = decide(m, seen(camera_ok=False), T0 + 180, POLICY, True)
    assert (action, state) == ("restart-source", "recovering")
    assert decide(m, seen(camera_ok=False), T0 + 300, POLICY, True)[1] == "recovering"
    action, state, message = decide(m, seen(camera_ok=False), T0 + 360, POLICY, True)
    assert (action, state) == ("reboot", "rebooting") and "kameraforrás" in message
    assert m["reboots"] == [T0 + 360]


def test_a_hung_source_restart_escalates_at_the_next_check():
    m = memory()
    decide(m, seen(camera_ok=False), T0, POLICY, True)
    assert decide(m, seen(camera_ok=False), T0 + 180, POLICY, True)[0] == "restart-source"
    m["source_restart_failed"] = True  # systemctl timed out: the camera process is stuck in the kernel
    action, _, message = decide(m, seen(camera_ok=False), T0 + 210, POLICY, True)
    assert action == "reboot" and "kernelben" in message


def test_recovery_reboot_is_forced_and_restarts_target_the_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0)
    )
    assert watchdog.act("reboot", "rdp", dry_run=False) == "ok"
    assert watchdog.act("restart-source", "rdp", dry_run=False) == "ok"
    assert calls == [
        ["systemctl", "reboot", "--force"],
        ["systemctl", "--machine=rdp@.host", "--user", "restart", "camera-source"],
    ]
    assert watchdog.act("reboot", "rdp", dry_run=True) == "dry-run" and len(calls) == 2


def test_boot_grace_waits_for_the_camera_but_not_for_a_hung_firmware():
    assert decide(memory(), seen(camera_ok=False, uptime=60), T0, POLICY, True)[0] is None
    action, _, message = decide(memory(), seen(camera_ok=False, firmware_hang=True, uptime=60), T0, POLICY, True)
    assert action == "reboot" and "firmware" in message


@pytest.mark.parametrize(
    "auto_reboot,reboots,reason",
    [
        (False, [], "ki van kapcsolva"),
        (True, [T0 - 600], "túl friss"),
        (True, [T0 - 80000, T0 - 50000, T0 - 20000], "24 órán belül"),
    ],
)
def test_reboots_are_switchable_and_rate_limited(auto_reboot, reboots, reason):
    m = memory() | {"reboots": list(reboots)}
    action, state, message = decide(m, seen(firmware_hang=True), T0, POLICY, auto_reboot)
    assert action is None and state == "blocked" and reason in message
    assert m["reboots"] == list(reboots)  # a blocked reboot is not counted


def test_old_reboots_expire_from_the_daily_limit():
    m = memory() | {"reboots": [T0 - 2 * 86400, T0 - 90000]}
    assert decide(m, seen(firmware_hang=True), T0, POLICY, True)[0] == "reboot"
    assert m["reboots"] == [T0]


def test_status_file_is_world_readable_and_auto_reboot_defaults_on(tmp_path):
    path = tmp_path / "watchdog-status.json"
    watchdog.write_json(path, {"state": "ok"})
    assert json.loads(path.read_text()) == {"state": "ok"}
    assert oct(os.stat(path).st_mode & 0o777) == "0o644"
    assert watchdog.auto_reboot_enabled(tmp_path / "missing.json") is True
    (tmp_path / "settings.json").write_text(json.dumps({"watchdog_auto_reboot": False}))
    assert watchdog.auto_reboot_enabled(tmp_path / "settings.json") is False


def test_settings_keep_every_key_when_one_changes(tmp_path):
    settings = RuntimeSettings(tmp_path)
    settings.set_rotation(180)
    settings.set_watchdog_auto_reboot(False)
    reloaded = RuntimeSettings(tmp_path)
    assert reloaded.rotation == 180 and reloaded.watchdog_auto_reboot is False
    with pytest.raises(ValueError):
        settings.set_watchdog_auto_reboot("no")


def report(tmp_path, age_seconds, **extra):
    updated = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(timespec="seconds")
    data = {"updated_at": updated, "interval": 30, "state": "ok", "message": "Minden rendben.", "firmware_ok": True}
    data.update(camera_ok=True, reboots_24h=1, history=[{"at": updated, "action": "reboot", "reason": "x"}], **extra)
    (tmp_path / "watchdog-status.json").write_text(json.dumps(data))


def test_web_reports_watchdog_and_toggles_auto_reboot(tmp_path):
    cfg = load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://e/l", "DATA_DIR": str(tmp_path)})
    store = EventStore(tmp_path)
    rt = SimpleNamespace(
        status=Status(cfg), store=store, settings=RuntimeSettings(tmp_path), start=lambda: None, close=lambda: None
    )
    with TestClient(create_app(cfg, rt)) as client:
        assert client.get("/api/status").json()["watchdog"] == {"installed": False}
        report(tmp_path, 10)
        current = client.get("/api/status").json()["watchdog"]
        assert current["installed"] and not current["stale"] and current["state"] == "ok"
        assert current["last_action"]["action"] == "reboot" and current["auto_reboot"] is True
        report(tmp_path, 600)
        assert client.get("/api/status").json()["watchdog"]["stale"] is True  # the service stopped reporting
        assert client.post("/api/settings/watchdog", json={"auto_reboot": False}).json() == {"auto_reboot": False}
        assert client.get("/api/status").json()["watchdog"]["auto_reboot"] is False
        plain = client.post(
            "/api/settings/watchdog", content='{"auto_reboot": true}', headers={"Content-Type": "text/plain"}
        )
        assert plain.status_code == 422
    assert json.loads((tmp_path / "settings.json").read_text())["watchdog_auto_reboot"] is False
    store.close()


def test_diagnostics_are_saved_before_a_recovery_and_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda command, **_: SimpleNamespace(stdout=f"out:{command[0]}", stderr="")
    )
    folder = tmp_path / "diagnostics"
    for index in range(4):
        watchdog.save_diagnostics(folder, "reboot", T0 + index, keep=3)
    files = sorted(folder.glob("*.txt"))
    assert len(files) == 3 and files[-1].name.endswith("-reboot.txt")
    text = files[-1].read_text()
    assert "$ uptime" in text and "dmesg" in text and "out:" in text
    assert oct(os.stat(files[-1]).st_mode & 0o777) == "0o644"
