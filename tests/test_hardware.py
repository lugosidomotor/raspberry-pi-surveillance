from types import SimpleNamespace

from app.hardware import HardwareMonitor


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def test_non_pi_has_no_hardware_panel(tmp_path):
    model = tmp_path / "model"
    write(model, "Apple Virtual Machine")
    assert HardwareMonitor(tmp_path, model_paths=[model]).snapshot() == {"is_raspberry_pi": False}


def test_pi_cpu_memory_temperature_disk_cache_and_warnings(tmp_path, monkeypatch):
    import app.hardware as hardware

    proc, sys = tmp_path / "proc", tmp_path / "sys"
    write(proc / "device-tree/model", "Raspberry Pi 4 Model B Rev 1.1\x00")
    write(proc / "stat", "cpu 100 0 0 100 0 0 0 0 200 0\n")
    write(proc / "meminfo", "MemTotal: 4000 kB\nMemAvailable: 2500 kB\n")
    write(proc / "uptime", "90061.0 100.0\n")
    write(sys / "class/thermal/thermal_zone0/temp", "78500\n")
    write(sys / "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "1500000\n")
    clock = [10]
    monkeypatch.setattr(hardware.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hardware.os, "getloadavg", lambda: (1.2, 0.5, 0.3))
    monkeypatch.setattr(hardware.shutil, "disk_usage", lambda _: SimpleNamespace(total=1000, used=950, free=50))
    monitor = HardwareMonitor(tmp_path, proc, sys)
    first = monitor.snapshot()
    assert first["is_raspberry_pi"] and first["model"].endswith("1.1")
    assert first["cpu_percent"] is None  # needs two distinct samples
    assert first["temperature_c"] == 78.5 and first["cpu_frequency_mhz"] == 1500
    assert first["memory"]["used_bytes"] == 1500 * 1024
    assert first["disk"]["free_bytes"] == 50 and len(first["warnings"]) == 2
    assert first["uptime_seconds"] == 90061
    write(proc / "stat", "cpu 130 0 0 170 0 0 0 0 999 0\n")
    assert monitor.snapshot()["cpu_percent"] is None  # cached, guest time ignored
    clock[0] += 5
    assert monitor.snapshot()["cpu_percent"] == 30


def test_pi_missing_or_invalid_sensors_are_not_fatal(tmp_path, monkeypatch):
    import app.hardware as hardware

    model = tmp_path / "mounted-model"
    write(model, "Raspberry Pi 4 Model B\x00")
    sys = tmp_path / "sys"
    write(sys / "class/thermal/thermal_zone0/temp", "999999\n")

    def unavailable(_):
        raise OSError("unavailable")

    monkeypatch.setattr(hardware.shutil, "disk_usage", unavailable)
    result = HardwareMonitor(tmp_path, tmp_path / "proc", sys, [model]).snapshot()
    assert result["is_raspberry_pi"]
    assert result["temperature_c"] is None and result["cpu_percent"] is None
    assert result["memory"] is None and result["disk"] is None
    assert result["uptime_seconds"] is None


def test_undervoltage_alarm_warns_and_flags_unreliable_frequency(tmp_path):
    proc, sys = tmp_path / "proc", tmp_path / "sys"
    write(proc / "device-tree/model", "Raspberry Pi 4 Model B\x00")
    write(sys / "class/hwmon/hwmon0/name", "cpu_thermal\n")
    write(sys / "class/hwmon/hwmon2/name", "rpi_volt\n")
    write(sys / "class/hwmon/hwmon2/in0_lcrit_alarm", "1\n")
    result = HardwareMonitor(tmp_path, proc, sys).snapshot()
    assert result["undervoltage"] is True
    assert "Feszültségesés: gyenge táp, a CPU lassít" in result["warnings"]
    write(sys / "class/hwmon/hwmon2/in0_lcrit_alarm", "0\n")
    assert HardwareMonitor(tmp_path, proc, sys).snapshot()["undervoltage"] is False
    assert HardwareMonitor(tmp_path, proc, tmp_path / "no-sys").snapshot()["undervoltage"] is None
