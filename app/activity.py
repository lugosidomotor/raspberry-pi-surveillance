"""Optional asynchronous person-clip capture and OpenRouter activity description."""

from __future__ import annotations

import base64
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
import time

import requests

from .recorder import error_summary, input_args, stop_process
from .utils import TaskWorker

log = logging.getLogger(__name__)
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_VIDEO_BYTES = 20 * 1024 * 1024
PROMPT = (
    "Assess whether clearly suspicious activity is visible in this 10-second security-camera clip. "
    "Return suspicious only for clearly observable behavior such as attempted unauthorized entry, tampering with a door/camera, concealment while trying to enter, or taking property. "
    "Ordinary arrival, delivery, walking, waiting, or carrying a package is not suspicious by itself. "
    "Do not identify the person, infer intent from appearance/time, or invent events. "
    "Write the activity and evidence values in Hungarian (magyarul), never in English. "
    "If the clip is unclear or too distant, choose not_suspicious and state that the evidence is inconclusive. "
    "Return only one JSON object without markdown, with exactly these keys: "
    '"verdict" (exactly "suspicious" or "not_suspicious"), "activity" (one concise Hungarian sentence), '
    '"evidence" (brief visible evidence in Hungarian), "uncertainty" (exactly "none", "some" or "high").'
)
# Reasoning models spend max_tokens on hidden reasoning first; a small cap leaves content empty.
MAX_TOKENS = 4096
VERDICTS = {"suspicious": "Gyanús", "not_suspicious": "Nem gyanús"}


def _text(value):
    if not isinstance(value, str) or len(value.strip()) < 3:
        return None
    value = value.strip()
    return value if len(value) <= 300 else value[:299].rstrip() + "…"


def parse_result(content):
    """Extract the verdict JSON, tolerating code fences, think tags or surrounding prose."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty model response")
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            result, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and "verdict" in result:
            break
    else:
        raise ValueError("model response contains no verdict JSON object")
    verdict = re.sub(r"[\s-]+", "_", str(result.get("verdict", "")).strip().lower())
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {str(result.get('verdict'))[:40]!r}")
    activity, evidence = _text(result.get("activity")), _text(result.get("evidence"))
    if not activity or not evidence:
        raise ValueError("model response is missing the activity or evidence text")
    uncertainty = str(result.get("uncertainty", "")).strip().lower()
    return VERDICTS[verdict], activity, evidence, uncertainty if uncertainty in ("none", "some", "high") else "some"


class ActivityAnalyzer:
    def __init__(self, cfg, store, status, notifier=None):
        self.cfg, self.store, self.status, self.notifier = cfg, store, status, notifier
        enabled = cfg["activity"]["enabled"]
        # Capture has its own worker so slow LLM calls never delay recording the scene of a new event.
        self.capture_worker = TaskWorker("activity-capture", self._record, capacity=16) if enabled else None
        self.worker = TaskWorker("activity-analysis", self._process, capacity=16) if enabled else None
        self.recent_silent = {}  # (label, zone) -> (monotonic time, clip path) of the latest silent event

    @property
    def enabled(self):
        return self.worker is not None

    def start(self):
        interrupted = self.store.fail_interrupted_activity()
        if interrupted:
            log.warning("activity analysis interrupted by restart: %d event(s) marked failed", interrupted)
        if self.worker:
            self.capture_worker.start()
            self.worker.start()

    def submit(self, event, analyze=True):
        if not self.worker:
            return False
        queued = dict(event)
        queued["activity_clip_path"] = self._clip_path(event)
        queued["analyze"] = analyze
        key = (event["label"], event["zone"])
        if not analyze:
            self.recent_silent[key] = (time.monotonic(), queued["activity_clip_path"])
        else:
            # Escalation of a silent episode: the model sees the minutes before the alert too. Captures run in
            # order, so the alert clip starts where the silent one ended and the joined clip is near-continuous.
            recent = self.recent_silent.pop(key, None)
            if recent and time.monotonic() - recent[0] <= self.cfg["events"]["cooldown_seconds"] + 5:
                queued["prefix_clip_path"] = recent[1]
        self.store.update(event["id"], activity_status="queued")
        if self.capture_worker.submit(queued):
            log.info("activity analysis queued: %s", event["id"])
            return True
        self.store.update(event["id"], activity_status="failed", activity_summary="Az elemzési sor megtelt.")
        return False

    def _clip_path(self, event):
        day = datetime.fromisoformat(event["timestamp"]).strftime("%Y-%m-%d")
        return f"events/{day}/{event['id']}_activity.mp4"

    def _capture(self, event, destination: Path):
        temporary = destination.with_suffix(".mp4.tmp")
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = input_args(self.cfg["camera"]["recording_url"], self.cfg["camera"]["transport"])
        command += [
            "-t",
            str(self.cfg["activity"]["clip_seconds"]),
            "-vf",
            "fps=5,scale=w='min(960,iw)':h=-2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(temporary),
        ]
        stderr = tempfile.TemporaryFile()
        process = None
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
            try:
                code = process.wait(timeout=self.cfg["activity"]["clip_seconds"] + 25)
            except subprocess.TimeoutExpired:
                stop_process(process)
                code = process.returncode
            if code != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("FFmpeg could not capture the activity clip")
            check = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            duration = float(check.stdout.strip())
            if not 8 <= duration <= 12:
                raise RuntimeError("Activity clip duration is outside 8–12 seconds")
            temporary.replace(destination)
            log.info("activity clip ready: %s (%d bytes)", event["id"], destination.stat().st_size)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
            temporary.unlink(missing_ok=True)
            detail = error_summary(stderr)
            log.warning("activity clip capture failed (%s)%s", type(exc).__name__, f": {detail}" if detail else "")
            raise
        finally:
            if process and process.poll() is None:
                stop_process(process)
            stderr.close()

    def _prepend(self, prefix: Path, clip: Path) -> bool:
        """Join ``prefix`` + ``clip`` into ``clip`` without re-encoding; both come from the same capture settings."""
        listing, joined = clip.with_suffix(".txt"), clip.with_suffix(".joined.mp4")
        try:
            listing.write_text(f"file '{prefix}'\nfile '{clip}'\n")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
                + ["-c", "copy", "-movflags", "+faststart", "-y", str(joined)],
                capture_output=True,
                timeout=30,
                check=True,
            )
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(joined)],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            if float(probe.stdout.strip()) < 15:
                raise RuntimeError("joined clip is too short")
            joined.replace(clip)
            return True
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
            log.warning("could not join the silent clip (%s); analysing the alert clip alone", type(exc).__name__)
            return False
        finally:
            listing.unlink(missing_ok=True)
            joined.unlink(missing_ok=True)

    def _request(self, model, video_url, prompt=PROMPT):
        timeout = self.cfg["activity"]["timeout_seconds"]
        # requests' timeout only bounds each socket read; enforce a wall-clock limit while reading the body.
        deadline = time.monotonic() + timeout
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {self.cfg['activity']['api_key']}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.1,
                "max_tokens": MAX_TOKENS,
                # A short visual verdict needs no hidden reasoning: measured on qwen3.7-flash, "low" took 75s
                # and 3.6k reasoning tokens, "none" 6s and ~0.27x the cost with an equally usable answer.
                "reasoning": {"effort": "none", "exclude": True},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "video_url", "video_url": {"url": video_url}},
                        ],
                    }
                ],
            },
            timeout=(10, timeout),
            stream=True,
        )
        with response:
            body = bytearray()
            for chunk in response.iter_content(1024):
                body += chunk
                if time.monotonic() > deadline:
                    raise requests.Timeout(f"no complete answer within {timeout}s")
        if not response.ok:
            detail = bytes(body[:300]).decode("utf-8", "replace")
            raise requests.HTTPError(f"HTTP {response.status_code}: {detail}", response=response)
        payload = json.loads(body)
        if payload.get("error"):
            raise ValueError(f"provider error: {str(payload['error'])[:300]}")
        choice = payload["choices"][0]
        content = choice["message"].get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not (content or "").strip() and choice.get("finish_reason") == "length":
            raise ValueError("token limit reached before the answer (reasoning used the whole budget)")
        return parse_result(content)

    def _record(self, event):
        event_id = event["id"]
        destination = self.store.root / event["activity_clip_path"]
        try:
            self.store.update(event_id, activity_status="recording")
            self._capture(event, destination)
            prefix = event.get("prefix_clip_path")
            if prefix and (self.store.root / prefix).is_file() and self._prepend(self.store.root / prefix, destination):
                event = {**event, "clip_seconds": 2 * self.cfg["activity"]["clip_seconds"]}
                log.info("activity clip joined with the preceding silent clip: %s", event_id)
            self.store.register_media(destination, "activity_clip")
        except Exception as exc:
            self._fail(event_id, "A klip rögzítése nem sikerült.")
            log.error("activity clip failed: %s (%s)", event_id, type(exc).__name__)
            return
        analyze = event.get("analyze", True)
        self.store.update(
            event_id,
            activity_clip_path=event["activity_clip_path"],
            activity_status="analyzing" if analyze else "captured",
            activity_clip_seconds=event.get("clip_seconds", self.cfg["activity"]["clip_seconds"]),
        )
        if not analyze:
            self.store.update(event_id, activity_notification_status="disabled")
            log.info("activity clip kept without analysis (silent event): %s", event_id)
            return
        if not self.worker.submit(event):
            self._fail(event_id, "Az elemzési sor megtelt.")

    def _fail(self, event_id, summary):
        # No verdict means no verdict notification; "disabled" keeps it from showing as queued forever.
        self.store.update(
            event_id, activity_status="failed", activity_summary=summary, activity_notification_status="disabled"
        )

    def _process(self, event):
        event_id = event["id"]
        path = self.store.root / event["activity_clip_path"]
        try:
            if path.stat().st_size > MAX_VIDEO_BYTES:
                raise ValueError("Activity clip exceeds the 20 MiB upload limit")
            video = "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            seconds = event.get("clip_seconds", self.cfg["activity"]["clip_seconds"])
            prompt = PROMPT.replace("10-second", f"{seconds}-second")
            failures = []
            for model in self.cfg["activity"]["models"]:
                started = time.monotonic()
                try:
                    verdict, activity, evidence, uncertainty = self._request(model, video, prompt)
                    break
                except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    failures.append(type(exc).__name__)
                    log.warning(
                        "activity model failed after %.0fs: %s (%s: %s)",
                        time.monotonic() - started,
                        model,
                        type(exc).__name__,
                        str(exc)[:300],
                    )
            else:
                raise RuntimeError("All configured activity models failed: " + ",".join(failures))
            uncertainty_note = " A felvétel alapján bizonytalan." if uncertainty == "high" else ""
            summary = f"{verdict} — {activity} Indok: {evidence}{uncertainty_note}"
            self.store.update(
                event_id,
                activity_verdict=verdict,
                activity_summary=summary,
                activity_status="completed",
                activity_model=model,
            )
            log.info("activity analysis completed in %.0fs: %s model=%s", time.monotonic() - started, event_id, model)
        except Exception as exc:
            self._fail(event_id, "A klip elemzése nem sikerült.")
            log.error("activity analysis failed: %s (%s: %s)", event_id, type(exc).__name__, str(exc)[:300])
            return
        # Outside the model loop: a notification problem must never trigger re-analysis with the next model.
        if self.notifier:
            self.notifier.send_activity({**event, "activity_verdict": verdict, "activity_summary": summary})
        else:
            self.store.update(event_id, activity_notification_status="disabled")

    def close(self, timeout=15):
        if not self.worker:
            return True
        captured = self.capture_worker.close(timeout / 2)
        return self.worker.close(timeout / 2) and captured
