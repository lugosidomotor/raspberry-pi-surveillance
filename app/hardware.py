"""Read-only Raspberry Pi host diagnostics; no privileged process or dependency."""

import os
from pathlib import Path
import shutil
import threading
import time


def read_text(path):
    try:
        return Path(path).read_text().replace("\x00", "").strip()
    except (OSError, UnicodeError):
        return ""


def number(path, scale=1):
    try:
        return float(read_text(path).split()[0]) * scale
    except (ValueError, IndexError):
        return None


class HardwareMonitor:
    def __init__(self, data_dir, proc_root=Path("/proc"), sys_root=Path("/sys"), model_paths=None):
        self.data_dir = Path(data_dir)
        self.proc = Path(proc_root)
        self.sys = Path(sys_root)
        candidates = (
            model_paths
            if model_paths is not None
            else [self.proc / "device-tree/model", self.sys / "firmware/devicetree/base/model", Path("/host/pi-model")]
        )
        self.model = next((model for path in candidates if "Raspberry Pi" in (model := read_text(path))), None)
        self.lock = threading.Lock()
        self.last_cpu = None
        self.cached = None
        self.sampled_at = float("-inf")

    def _cpu(self):
        try:
            line = read_text(self.proc / "stat").splitlines()[0].split()
            if line[0] != "cpu":
                return None
            values = [int(value) for value in line[1:9]]  # guest time is already included in user/nice
            current = (sum(values), values[3] + values[4])
        except (ValueError, IndexError):
            return None
        previous, self.last_cpu = self.last_cpu, current
        if previous is None or current[0] <= previous[0] or current[1] < previous[1]:
            return None
        return round(max(0, min(100, 100 * (1 - (current[1] - previous[1]) / (current[0] - previous[0])))), 1)

    def _memory(self):
        values = {}
        for line in read_text(self.proc / "meminfo").splitlines():
            parts = line.split()
            try:
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
            except (ValueError, IndexError):
                continue
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if not total or available is None:
            return None
        available = max(0, min(total, available))
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "used_percent": round((total - available) / total * 100, 1),
        }

    def _disk(self):
        try:
            usage = shutil.disk_usage(self.data_dir)
            return {
                "total_bytes": usage.total,
                "free_bytes": usage.free,
                "used_bytes": usage.used,
                "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                "path": str(self.data_dir),
            }
        except OSError:
            return None

    def _undervoltage(self):
        # cpufreq keeps reporting the requested clock while the firmware throttles a weak supply;
        # the rpi_volt alarm is the unprivileged view of that state (vcgencmd needs /dev/vcio).
        for hwmon in sorted((self.sys / "class/hwmon").glob("hwmon*")):
            if read_text(hwmon / "name") == "rpi_volt":
                return read_text(hwmon / "in0_lcrit_alarm") == "1"
        return None

    def snapshot(self):
        if self.model is None:
            return {"is_raspberry_pi": False}
        with self.lock:
            now = time.monotonic()
            if self.cached is not None and now - self.sampled_at < 5:
                return self.cached.copy()
            temperature = number(self.sys / "class/thermal/thermal_zone0/temp", 0.001)
            if temperature is not None and not -10 <= temperature <= 150:
                temperature = None
            frequency = number(self.sys / "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", 0.001)
            try:
                load = [round(value, 2) for value in os.getloadavg()]
            except OSError:
                load = None
            memory, disk = self._memory(), self._disk()
            undervoltage = self._undervoltage()
            warnings = []
            if undervoltage:
                warnings.append("Feszültségesés: gyenge táp, a CPU lassít")
            if temperature is not None and temperature >= 75:
                warnings.append("Magas CPU-hőmérséklet")
            if disk and disk["free_bytes"] < disk["total_bytes"] * 0.1:
                warnings.append("Kevés szabad tárhely (10% alatt)")
            self.cached = {
                "is_raspberry_pi": True,
                "model": self.model,
                "scope": "host",
                "sampled_at": time.time(),
                "temperature_c": round(temperature, 1) if temperature is not None else None,
                "cpu_percent": self._cpu(),
                "cpu_cores": os.cpu_count(),
                "cpu_frequency_mhz": frequency,
                "undervoltage": undervoltage,
                "load_average": load,
                "memory": memory,
                "disk": disk,
                "uptime_seconds": number(self.proc / "uptime"),
                "warnings": warnings,
            }
            self.sampled_at = now
            return self.cached.copy()
