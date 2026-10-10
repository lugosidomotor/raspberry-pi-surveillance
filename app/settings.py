"""Runtime settings changed from the UI; persisted in the data directory, applied without restart."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading

log = logging.getLogger(__name__)
ROTATIONS = (0, 90, 180, 270)


class RuntimeSettings:
    def __init__(self, data_dir, default_rotation=0):
        self.path = Path(data_dir) / "settings.json"
        self.lock = threading.Lock()
        # The host recovery watchdog reads watchdog_auto_reboot from the same file.
        self._data = {"rotation": default_rotation, "watchdog_auto_reboot": True}
        try:
            stored = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("runtime settings unreadable (%s); using defaults", type(exc).__name__)
            return
        if not isinstance(stored, dict):
            return
        # A value chosen in the UI outranks the environment default.
        if stored.get("rotation") in ROTATIONS and not isinstance(stored.get("rotation"), bool):
            self._data["rotation"] = stored["rotation"]
        if isinstance(stored.get("watchdog_auto_reboot"), bool):
            self._data["watchdog_auto_reboot"] = stored["watchdog_auto_reboot"]

    @property
    def rotation(self) -> int:
        with self.lock:
            return self._data["rotation"]

    @property
    def watchdog_auto_reboot(self) -> bool:
        with self.lock:
            return self._data["watchdog_auto_reboot"]

    def _save(self, **changes):
        with self.lock:
            data = {**self._data, **changes}
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())  # a power cut right after a UI change must not lose it
            temporary.replace(self.path)
            self._data = data

    def set_rotation(self, value: int) -> int:
        if value not in ROTATIONS or isinstance(value, bool):
            raise ValueError("rotation must be 0, 90, 180 or 270")
        self._save(rotation=value)
        log.info("camera rotation set to %d°", value)
        return value

    def set_watchdog_auto_reboot(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("auto_reboot must be true or false")
        self._save(watchdog_auto_reboot=enabled)
        log.info("watchdog automatic reboot %s", "enabled" if enabled else "disabled")
        return enabled
