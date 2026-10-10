"""Age-based local retention; never deletes active recordings or in-flight uploads."""

import json
import logging
import shutil
import time

log = logging.getLogger(__name__)


class StoragePolicy:
    def __init__(self, cfg, store, status):
        self.cfg, self.store, self.status = cfg, store, status
        self.next_check = self.next_sweep = 0
        self.low = False

    def poll(self):
        now = time.monotonic()
        if now >= self.next_check:
            self.next_check = now + 10
            low = shutil.disk_usage(self.store.root).free < self.cfg["min_free_mb"] * 1024**2
            if low != self.low:
                log.warning("storage low; new snapshots/recordings paused" if low else "storage recovered")
            self.low = low
            self.status.update(storage_low=low)
        if now >= self.next_sweep:
            self.next_sweep = now + 3600
            self.sweep(time.time())
        return not self.low

    def sweep(self, now):
        cutoff = now - self.cfg["retention_days"] * 86400
        removed = 0
        # The shared lock prevents an uploader claiming a file while it is removed.
        with self.store.lock, self.store.db:
            busy = {r[0] for r in self.store.db.execute("SELECT path FROM media WHERE status='uploading'")}
            deferred = set()
            for row in self.store.db.execute("SELECT payload FROM events WHERE epoch<?", (cutoff,)).fetchall():
                event = json.loads(row[0])
                keys = ("snapshot_path", "thumbnail_path", "recording_path", "activity_clip_path", "montage_path")
                paths = [event.get(k) for k in keys]
                if event.get("recording_status") == "recording" or any(p in busy for p in paths):
                    deferred.update(p for p in paths if p)
                    continue
                for relative in paths:
                    if relative:
                        removed += self._remove(relative)
                self.store.db.execute("DELETE FROM events WHERE id=?", (event["id"],))
            for directory in ("events", "recordings"):
                root = self.store.root / directory
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if path.is_symlink() or not path.is_file() or path.suffix not in (".jpg", ".mp4", ".mkv"):
                        continue
                    relative = str(path.relative_to(self.store.root))
                    if relative not in busy | deferred and path.stat().st_mtime < cutoff:
                        removed += self._remove(relative)
                # Only empty directories, never recursive deletion of broad paths.
                for path in sorted(root.rglob("*"), reverse=True):
                    if path.is_dir() and not path.is_symlink():
                        try:
                            path.rmdir()
                        except OSError:
                            pass
        if removed:
            log.info("retention removed %s expired media files (local only)", removed)

    def _remove(self, relative):
        path = self.store.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.store.root):
            return 0
        if path.suffix not in (".jpg", ".mp4", ".mkv"):
            return 0
        existed = path.exists()
        path.unlink(missing_ok=True)
        self.store.db.execute("DELETE FROM media WHERE path=?", (relative,))
        return int(existed)
