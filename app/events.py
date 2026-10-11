"""Persistent SQLite event catalog, atomic snapshots, and finalized media catalog."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import uuid

import cv2
from .source import source_identity

log = logging.getLogger(__name__)


class EventStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "events.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_time ON events(timestamp DESC);
            CREATE TABLE IF NOT EXISTS media (path TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                                             attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0);
        """)
        self.db.commit()
        # Additive migration: retain old IDs/files while fixing ordering across DST.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        for name, kind in (("epoch", "REAL"), ("label", "TEXT"), ("zone", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE events ADD COLUMN {name} {kind}")
        for row in self.db.execute("SELECT id,timestamp,payload FROM events WHERE epoch IS NULL").fetchall():
            event = json.loads(row["payload"])
            parsed = datetime.fromisoformat(row["timestamp"])
            self.db.execute(
                "UPDATE events SET epoch=?,label=?,zone=? WHERE id=?",
                (parsed.timestamp(), event.get("label"), event.get("zone"), row["id"]),
            )
        self.db.executescript(
            "CREATE INDEX IF NOT EXISTS event_epoch ON events(epoch DESC);"
            "CREATE INDEX IF NOT EXISTS event_label_epoch ON events(label,epoch DESC);"
        )
        self.db.execute("UPDATE media SET status='pending' WHERE status='uploading'")
        self.db.commit()

    def add(self, event):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO events(id,timestamp,payload,epoch,label,zone) VALUES (?,?,?,?,?,?)",
                (
                    event["id"],
                    event["timestamp"],
                    json.dumps(event),
                    datetime.fromisoformat(event["timestamp"]).timestamp(),
                    event["label"],
                    event["zone"],
                ),
            )

    def update(self, event_id, **changes):
        with self.lock, self.db:
            row = self.db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
            if row:
                event = json.loads(row[0])
                event.update(changes)
                self.db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(event), event_id))

    def fail_interrupted_activity(self):
        """Clip analyses do not resume after a restart; settle them instead of leaving them in progress forever."""
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM events WHERE json_extract(payload, '$.activity_status') IN "
                "('queued', 'recording', 'analyzing')"
            ).fetchall()
        for (event_id,) in rows:
            self.update(
                event_id,
                activity_status="failed",
                activity_summary="Az elemzés újraindítás miatt megszakadt.",
                activity_notification_status="disabled",
            )
        return len(rows)

    def fail_interrupted_montages(self):
        """A montage episode lives in memory only; after a restart it can never complete."""
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM events WHERE json_extract(payload, '$.montage_status') = 'recording'"
            ).fetchall()
        for (event_id,) in rows:
            self.update(event_id, montage_status="failed")
        return len(rows)

    def list_events(self, limit=50, offset=0, label=None):
        with self.lock:
            if label:
                rows = self.db.execute(
                    "SELECT payload FROM events WHERE label=? ORDER BY epoch DESC,id DESC LIMIT ? OFFSET ?",
                    (label, limit, offset),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT payload FROM events ORDER BY epoch DESC,id DESC LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def timeline(self, start, end):
        """Exact daily counts and newest evidence per 15-minute bucket (bounded response)."""
        with self.lock:
            rows = self.db.execute(
                """WITH ranked AS (
                    SELECT payload, CAST((epoch-?)/900 AS INTEGER) AS bucket,
                           COUNT(*) OVER (PARTITION BY CAST((epoch-?)/900 AS INTEGER)) AS count,
                           ROW_NUMBER() OVER (
                               PARTITION BY CAST((epoch-?)/900 AS INTEGER)
                               ORDER BY epoch DESC,id DESC
                           ) AS position
                    FROM events WHERE epoch>=? AND epoch<?
                ) SELECT bucket,count,payload FROM ranked WHERE position=1 ORDER BY bucket""",
                (start, start, start, start, end),
            ).fetchall()
        return [{"index": r["bucket"], "count": r["count"], "event": json.loads(r["payload"])} for r in rows]

    def label_counts(self, start, end):
        """Events per object label in [start, end): the day summary of the UI."""
        with self.lock:
            rows = self.db.execute(
                "SELECT label, COUNT(*) AS count FROM events WHERE epoch>=? AND epoch<? GROUP BY label", (start, end)
            ).fetchall()
        return {r["label"]: r["count"] for r in rows}

    def register_media(self, path: Path, kind: str):
        resolved = path.resolve()
        relative = str(resolved.relative_to(self.root))
        if resolved.suffix.lower() not in (".jpg", ".mp4", ".mkv") or not resolved.is_file():
            raise ValueError("Only finalized JPEG/MP4/MKV files can enter the upload catalog")
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO media(path,kind) VALUES (?,?)", (relative, kind))

    def pending_media(self, now, limit=8):
        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM media WHERE status IN ('pending','failed') AND next_attempt<=? LIMIT ?", (now, limit)
                ).fetchall()
            ]

    def upload_result(self, path, success, next_attempt=0):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE media SET status=?, attempts=attempts+1, next_attempt=? WHERE path=?",
                ("uploaded" if success else "failed", next_attempt, path),
            )

    def claim_media(self, path):
        with self.lock, self.db:
            return (
                self.db.execute(
                    "UPDATE media SET status='uploading' WHERE path=? AND status IN ('pending','failed')", (path,)
                ).rowcount
                == 1
            )

    def missing_media(self, path):
        with self.lock, self.db:
            self.db.execute("UPDATE media SET status='missing' WHERE path=?", (path,))

    def close(self):
        with self.lock:
            self.db.close()


def annotate(frame, detections, timestamp, zone=""):
    image = frame.copy()
    for detection in detections:
        x1, y1, x2, y2 = detection.box
        cv2.rectangle(image, (x1, y1), (x2, y2), (50, 220, 70), 2)
        cv2.putText(
            image,
            f"{detection.label} {detection.confidence:.2f} {zone}",
            (x1, max(24, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (50, 220, 70),
            2,
        )
    cv2.putText(
        image,
        timestamp.isoformat(timespec="seconds"),
        (10, image.shape[0] - 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        2,
    )
    return image


class EventManager:
    def __init__(self, cfg, store, recorder, notification_worker, status, activity_analyzer=None, montage=None):
        self.cfg, self.store, self.recorder = cfg, store, recorder
        self.worker, self.status = notification_worker, status
        self.activity = activity_analyzer
        self.montage = montage
        self.last_events = {}

    def emit(self, frame, detection, zone, now, timestamp=None, alert=True):
        """``alert=False`` records a low-confidence (silent) event: evidence is kept, nobody is notified."""
        key = (detection.label, zone.name)
        cooldown = self.cfg["events"]["cooldown_seconds"]
        if now - self.last_events.get(key, float("-inf")) < cooldown:
            return None  # an alert already covers this moment, for silent events too
        if not alert and now - self.last_events.get(("silent", *key), float("-inf")) < cooldown:
            return None  # a silent event never blocks an escalation to an alert
        timestamp = timestamp or datetime.now(timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        label = re.sub(r"[^A-Za-z0-9_-]", "_", detection.label)
        event_id = f"{timestamp:%Y-%m-%d_%H-%M-%S-%f}_{label}_{detection.confidence:.2f}_{uuid.uuid4().hex[:8]}"
        directory = self.store.root / "events" / timestamp.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{event_id}.jpg"
        temporary = path.with_suffix(".tmp")
        source = source_identity(self.cfg)
        image = annotate(frame, [detection], timestamp, zone.name)
        if source["is_test"]:
            cv2.rectangle(image, (0, 0), (260, 40), (0, 200, 255), -1)
            cv2.putText(image, "TESZT STREAM", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 30, 40), 2)
        # Quality 85 is about half the size of OpenCV's default 95 with no visible difference.
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise OSError("JPEG encoding failed")
        temporary.write_bytes(encoded.tobytes())
        temporary.replace(path)
        thumbnail = path.with_name(path.stem + "_thumb.jpg")
        height, width = image.shape[:2]
        thumb_width = min(320, width)
        small = cv2.resize(image, (thumb_width, max(1, round(height * thumb_width / width))))
        ok, thumb_encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            thumb_tmp = thumbnail.with_suffix(".tmp")
            thumb_tmp.write_bytes(thumb_encoded.tobytes())
            thumb_tmp.replace(thumbnail)
        event = {
            "id": event_id,
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            "label": detection.label,
            "confidence": round(detection.confidence, 4),
            "is_test": source["is_test"],
            "box": list(detection.box),
            "zone": zone.name,
            "action": zone.action,
            "snapshot_path": str(path.relative_to(self.store.root)),
            "thumbnail_path": str(thumbnail.relative_to(self.store.root)) if ok else None,
            "recording_path": None,
            "recording_status": "disabled",
            "alert": alert,
            "notification_status": ("queued" if self.worker else "disabled") if alert else "silent",
            "activity_status": "queued"
            if self.activity and self.activity.enabled and detection.label == "person"
            else "disabled",
            "activity_notification_status": "queued"
            if alert
            and self.activity
            and self.activity.enabled
            and detection.label == "person"
            and self.activity.notifier
            else "disabled",
            "activity_summary": None,
            "activity_clip_path": None,
            "montage_status": "recording"
            if self.montage and self.montage.enabled and detection.label == "person"
            else "disabled",
            "montage_path": None,
        }
        self.store.add(event)
        self.store.register_media(path, "snapshot")
        self.last_events[key if alert else ("silent", *key)] = now
        if self.activity and self.activity.enabled and detection.label == "person":
            # Silent events keep the clip as evidence but skip the paid/quota-limited LLM analysis.
            self.activity.submit(event, analyze=alert)
        if self.montage and self.montage.enabled and detection.label == "person":
            self.montage.begin(event)
        if self.cfg["recording"]["events"]:
            recording = self.recorder.start_clip(directory / f"{event_id}.mp4", event_id)
            event.update(
                recording_path=str(recording.relative_to(self.store.root)) if recording else None,
                recording_status="recording" if recording else "failed",
            )
            self.store.update(
                event_id, recording_path=event["recording_path"], recording_status=event["recording_status"]
            )
        if alert and self.worker and not self.worker.submit(event):
            event["notification_status"] = "queue_full"
            self.store.update(event_id, notification_status="queue_full")
        self.status.update(last_event=event)
        log.info("snapshot saved: %s", path)
        log.info(
            "%s event: %s %.2f zone=%s",
            "valid" if alert else "silent",
            detection.label,
            detection.confidence,
            zone.name,
        )
        return event
