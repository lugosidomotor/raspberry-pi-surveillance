"""Optional ntfy provider; bounded requests are called from a separate worker."""

from abc import ABC, abstractmethod
import base64
import logging
import threading
import time

import cv2
import requests

log = logging.getLogger(__name__)
VERDICT_ICONS = {"Gyanús": "🛑", "Nem gyanús": "✅"}


def mime_header(value):
    """RFC 2047-encode non-ASCII text: ntfy decodes it, while http.client can only send Latin-1 headers."""
    return value if value.isascii() else "=?UTF-8?B?" + base64.b64encode(value.encode()).decode("ascii") + "?="


class Notifier(ABC):
    @abstractmethod
    def send(self, event):
        """Send an event; return sent / cooldown / failed."""


# The first alert carries a phone-sized copy; the full snapshot stays in the app and backup.
ALERT_IMAGE_WIDTH = 640


def alert_image(path):
    """Downscaled JPEG bytes for the instant alert, or None to fall back to the original file."""
    image = cv2.imread(str(path))
    if image is None:
        return None
    height, width = image.shape[:2]
    if width > ALERT_IMAGE_WIDTH:
        image = cv2.resize(image, (ALERT_IMAGE_WIDTH, round(height * ALERT_IMAGE_WIDTH / width)))
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return encoded.tobytes() if ok else None


class NtfyNotifier(Notifier):
    def __init__(self, cfg, store, status):
        self.cfg, self.store, self.status = cfg, store, status
        self.last_sent = {}
        # Each worker thread keeps its own keep-alive session: no TLS handshake per alert.
        self.local = threading.local()

    def _post(self, **kwargs):
        session = getattr(self.local, "session", None)
        if session is None:
            session = self.local.session = requests.Session()
        return session.post(f"{self.cfg['url']}/{self.cfg['topic']}", **kwargs)

    def send(self, event):
        key = (event["label"], event["zone"])
        now = time.monotonic()
        if now - self.last_sent.get(key, float("-inf")) < self.cfg["cooldown_seconds"]:
            self.store.update(event["id"], notification_status="cooldown")
            return "cooldown"
        headers = {
            "Title": "TEST STREAM - Camera alert" if event.get("is_test") else "Camera alert",
            "Priority": "high",
        }
        if self.cfg["token"]:
            headers["Authorization"] = f"Bearer {self.cfg['token']}"
        message = f"{event['label'].capitalize()} detected - {event['confidence']:.0%} ({event['zone']})"
        snapshot = self.store.root / event["snapshot_path"]
        try:
            if snapshot.is_file():
                headers.update({"X-Filename": snapshot.name, "Message": message})
                image = alert_image(snapshot)
                if image is not None:
                    response = self._post(data=image, headers=headers, timeout=(3, 7))
                else:
                    with snapshot.open("rb") as handle:
                        response = self._post(data=handle, headers=headers, timeout=(3, 7))
            else:
                response = self._post(data=message.encode(), headers=headers, timeout=(3, 7))
            response.raise_for_status()
            self.last_sent[key] = time.monotonic()
            result = "sent"
            log.info("notification sent: %s zone=%s", event["label"], event["zone"])
        except (requests.RequestException, OSError) as exc:
            result = "failed"
            log.warning("notification failed (%s)", type(exc).__name__)
        self.store.update(event["id"], notification_status=result)
        self.status.update(notification_status=result)
        return result

    def send_montage(self, event, path, frames, start, end):
        """Attach the end-of-motion montage; independent of the per-event alert cooldown."""
        title = f"🎞️ Mozgás vége · {frames} kép" + (" · TESZT" if event.get("is_test") else "")
        headers = {
            "Title": mime_header(title),
            "Message": mime_header(f"{event['label']} · {event['zone']} · {start:%H:%M:%S}–{end:%H:%M:%S}"),
            "X-Filename": path.name,
            "Priority": "default",
        }
        if self.cfg["token"]:
            headers["Authorization"] = f"Bearer {self.cfg['token']}"
        try:
            with path.open("rb") as handle:
                response = self._post(data=handle, headers=headers, timeout=(3, 20))
            response.raise_for_status()
            result = "sent"
            log.info("montage notification sent: %s frames=%d", event["id"], frames)
        except (requests.RequestException, OSError) as exc:
            result = "failed"
            log.warning("montage notification failed (%s)", type(exc).__name__)
        self.store.update(event["id"], montage_notification_status=result)
        return result

    def send_activity(self, event):
        """Send a concise, separate verdict notification after clip analysis."""
        verdict = event.get("activity_verdict")
        summary = event.get("activity_summary")
        if verdict not in VERDICT_ICONS or not summary:
            return "skipped"
        title = f"{VERDICT_ICONS[verdict]} {verdict} · kamerafigyelés" + (" · TESZT" if event.get("is_test") else "")
        headers = {
            "Title": mime_header(title),
            "Priority": "high" if verdict == "Gyanús" else "default",
        }
        if self.cfg["token"]:
            headers["Authorization"] = f"Bearer {self.cfg['token']}"
        # Text-only delivery keeps the verdict and reason readable in watch previews.
        message = summary.strip()[:500]
        try:
            response = self._post(data=message.encode("utf-8"), headers=headers, timeout=(3, 7))
            response.raise_for_status()
            result = "sent"
            log.info("activity notification sent: %s verdict=%s", event["id"], verdict)
        except (requests.RequestException, OSError) as exc:
            result = "failed"
            log.warning("activity notification failed (%s)", type(exc).__name__)
        self.store.update(event["id"], activity_notification_status=result)
        self.status.update(activity_notification_status=result)
        return result
