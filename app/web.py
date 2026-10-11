"""Small FastAPI UI; read-only except two UI settings. RTSP credentials are never exposed in API responses."""

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import json
import math
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .runtime import Runtime
from .hardware import HardwareMonitor
from .source import source_identity

STATIC = Path(__file__).parent / "static"
mimetypes.add_type("font/woff2", ".woff2")  # the self-hosted UI font; older Pythons serve it untyped


class Rotation(BaseModel):
    rotation: int


class WatchdogSettings(BaseModel):
    auto_reboot: bool


def watchdog_status(data_dir: Path, settings) -> dict:
    """The host recovery watchdog's last report, marked stale when it stopped reporting."""
    try:
        report = json.loads((data_dir / "watchdog-status.json").read_text())
        updated = datetime.fromisoformat(report["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"installed": False}
    if not isinstance(report, dict):
        return {"installed": False}
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    public = {key: report.get(key) for key in ("state", "message", "firmware_ok", "camera_ok", "reboots_24h")}
    public.update(installed=True, updated_at=report["updated_at"], stale=age > 3 * report.get("interval", 30))
    history = report.get("history") or []
    public["last_action"] = history[-1] if history else None
    # The UI toggle is the source of truth; the report may lag one check behind it.
    public["auto_reboot"] = settings.watchdog_auto_reboot if settings else report.get("auto_reboot", True)
    return public


def public_event(event):
    result = dict(event)
    result["snapshot_url"] = "/media/" + quote(event["snapshot_path"], safe="/")
    result["thumbnail_url"] = (
        "/media/" + quote(event["thumbnail_path"], safe="/") if event.get("thumbnail_path") else result["snapshot_url"]
    )
    recording = event.get("recording_path")
    result["recording_url"] = (
        "/media/" + quote(recording, safe="/") if recording and event.get("recording_status") == "completed" else None
    )
    montage = event.get("montage_path")
    result["montage_url"] = (
        "/media/" + quote(montage, safe="/") if montage and event.get("montage_status") == "completed" else None
    )
    activity_clip = event.get("activity_clip_path")
    result["activity_video_url"] = (
        "/media/" + quote(activity_clip, safe="/")
        if activity_clip and event.get("activity_status") in ("analyzing", "completed", "failed", "captured")
        else None
    )
    return result


def create_app(cfg, runtime=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = runtime or Runtime(cfg)
        app.state.runtime.start()
        app.state.hardware = HardwareMonitor(cfg["data_dir"])
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.runtime.close)

    app = FastAPI(title="Camera Stack", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "private, max-age=86400"
            if request.url.path.startswith("/media/") and response.status_code in (200, 206, 304)
            else "no-store"
        )
        return response

    @app.get("/health")
    def health(request: Request):
        rt = request.app.state.runtime
        state = rt.status.snapshot()
        failed = state["pipeline"] == "failed"
        return JSONResponse(
            {
                "status": "error" if failed else "ok",
                "camera": state["camera"],
                "ai": state["ai"],
                "pipeline": state["pipeline"],
            },
            status_code=503 if failed else 200,
        )

    @app.get("/api/status")
    def status(request: Request):
        rt = request.app.state.runtime
        state = rt.status.snapshot()
        events = rt.store.list_events(1)
        state["last_event"] = public_event(events[0]) if events else None
        state["live_stream_url"] = cfg["web"]["live_stream_url"]
        state["hardware"] = request.app.state.hardware.snapshot()
        state["source"] = source_identity(cfg)
        state["activity_analysis_enabled"] = cfg["activity"]["enabled"]
        settings = getattr(rt, "settings", None)
        state["rotation"] = settings.rotation if settings else cfg["camera"]["rotation"]
        state["watchdog"] = watchdog_status(Path(cfg["data_dir"]), settings)
        return state

    # The only write: a JSON body forces a CORS preflight, so other websites cannot change it via the browser.
    @app.post("/api/settings/rotation")
    def set_rotation(request: Request, body: Rotation):
        settings = getattr(request.app.state.runtime, "settings", None)
        if settings is None:
            raise HTTPException(503, "Runtime settings unavailable")
        try:
            return {"rotation": settings.set_rotation(body.rotation)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(500, "Could not save the rotation") from None

    @app.post("/api/settings/watchdog")
    def set_watchdog(request: Request, body: WatchdogSettings):
        settings = getattr(request.app.state.runtime, "settings", None)
        if settings is None:
            raise HTTPException(503, "Runtime settings unavailable")
        try:
            return {"auto_reboot": settings.set_watchdog_auto_reboot(body.auto_reboot)}
        except OSError:
            raise HTTPException(500, "Could not save the watchdog setting") from None

    @app.get("/api/events")
    def events(
        request: Request,
        limit: int = Query(24, ge=1, le=100),
        offset: int = Query(0, ge=0),
        label: str | None = Query(None, max_length=64),
    ):
        rt = request.app.state.runtime
        return {
            "events": [public_event(e) for e in rt.store.list_events(limit, offset, label)],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/timeline")
    def timeline(request: Request, start: float = Query(..., ge=0), end: float = Query(..., ge=0)):
        # Browser-local midnight boundaries accommodate 23/25-hour DST days.
        if not math.isfinite(start) or not math.isfinite(end) or not 0 < end - start <= 26 * 3600:
            raise HTTPException(422, "Timeline range must be finite and at most 26 hours")
        store = request.app.state.runtime.store
        buckets = store.timeline(start, end)
        return {
            "start": start,
            "end": end,
            "bucket_seconds": 900,
            "total": sum(b["count"] for b in buckets),
            "labels": store.label_counts(start, end),
            "buckets": [{**b, "event": public_event(b["event"])} for b in buckets],
        }

    @app.get("/media/{relative_path:path}")
    def media(request: Request, relative_path: str):
        root = request.app.state.runtime.store.root
        path = (root / relative_path).resolve()
        try:
            normalized = path.relative_to(root)
        except ValueError:
            raise HTTPException(404, "Media not found") from None
        if (
            not normalized.parts
            or normalized.parts[0] not in ("events", "recordings")
            or path.suffix.lower() not in (".jpg", ".mp4", ".mkv")
            or not path.is_file()
        ):
            raise HTTPException(404, "Media not found or archived")
        return FileResponse(path)

    if cfg["web"]["enabled"]:
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        @app.get("/events")
        def page():
            return FileResponse(STATIC / "index.html")

    return app
