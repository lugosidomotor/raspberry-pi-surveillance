"""Defaults < YAML < environment; integrations are activated by environment only."""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

DEFAULTS = {
    "camera": {
        "url": "",
        "transport": "tcp",
        "test_stream": False,
        "open_timeout_seconds": 8,
        "read_timeout_seconds": 5,
        "stale_seconds": 8,
        "reconnect_max_seconds": 30,
        "recording_url": "",
        "decoder": "",
        "rotation": 0,
    },
    "motion": {
        "enabled": True,
        "max_fps": 8,
        "resize_width": 640,
        "min_area": 3000,
        "threshold": 25,
        "required_frames": 3,
        "warmup_frames": 30,
        "cooldown_seconds": 10,
        "history": 500,
        "learning_rate": -1,
        "crop_padding": 0.35,
    },
    "ai": {
        "enabled": True,
        "backend": "ultralytics",
        "model": "yolo11n.pt",
        "device": "auto",
        "image_size": 320,
        "confidence": 0.75,
        "confidence_by_class": {},
        "record_confidence": 0,
        "max_fps": 3,
        "crop_motion": True,
        "tracked_objects": ["person"],
        "cpu_threads": 2,
    },
    "validation": {"positive_frames": 2, "window_frames": 3, "max_gap_seconds": 5, "instant_confidence": 0},
    "events": {"cooldown_seconds": 30},
    "storage": {"retention_days": 14, "min_free_mb": 512},
    "recording": {
        "events": False,
        "clip_seconds": 30,
        "continuous": False,
        "segment_seconds": 300,
        "pre_roll_seconds": 5,
    },
    "notifications": {"enabled": False, "url": "", "topic": "", "token": "", "cooldown_seconds": 60},
    "backup": {
        "enabled": False,
        "remote": "",
        "path": "",
        "delete_after_upload": False,
        "scan_seconds": 30,
        "timeout_seconds": 120,
    },
    "activity": {
        "enabled": False,
        "api_key": "",
        "models": [
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            # Paid fallback (~$0.0002 per clip) for when the shared free pools are rate-limited.
            "qwen/qwen3.7-flash",
        ],
        "clip_seconds": 10,
        "timeout_seconds": 75,
    },
    "montage": {
        "enabled": True,
        "interval_seconds": 1.0,
        "max_frames": 16,
        "max_seconds": 300,
        "idle_seconds": 60,
        "tile_width": 480,
    },
    "web": {"enabled": True, "host": "0.0.0.0", "port": 8000, "live_stream_url": ""},
    "logging": {"level": "INFO", "stats_interval_seconds": 30},
    "data_dir": "./data",
    "zones": [],
}


class ConfigError(ValueError):
    pass


def boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise ConfigError("expected true or false")


# Every supported variable is documented in .env.example.
ENV_MAP = {
    "TEST_STREAM": ("camera", "test_stream", boolean),
    "CAMERA_TRANSPORT": ("camera", "transport", str),
    "CAMERA_OPEN_TIMEOUT_SECONDS": ("camera", "open_timeout_seconds", float),
    "CAMERA_READ_TIMEOUT_SECONDS": ("camera", "read_timeout_seconds", float),
    "CAMERA_STALE_SECONDS": ("camera", "stale_seconds", float),
    "CAMERA_RECONNECT_MAX_SECONDS": ("camera", "reconnect_max_seconds", float),
    "CAMERA_DECODER": ("camera", "decoder", str),
    "CAMERA_ROTATION": ("camera", "rotation", int),
    "MOTION_ENABLED": ("motion", "enabled", boolean),
    "MOTION_MAX_FPS": ("motion", "max_fps", float),
    "MOTION_MIN_AREA": ("motion", "min_area", float),
    "MOTION_THRESHOLD": ("motion", "threshold", float),
    "MOTION_REQUIRED_FRAMES": ("motion", "required_frames", int),
    "MOTION_WARMUP_FRAMES": ("motion", "warmup_frames", int),
    "MOTION_COOLDOWN_SECONDS": ("motion", "cooldown_seconds", float),
    "MOTION_RESIZE_WIDTH": ("motion", "resize_width", int),
    "MOTION_HISTORY": ("motion", "history", int),
    "MOTION_LEARNING_RATE": ("motion", "learning_rate", float),
    "MOTION_CROP_PADDING": ("motion", "crop_padding", float),
    "AI_ENABLED": ("ai", "enabled", boolean),
    "AI_BACKEND": ("ai", "backend", str),
    "AI_CPU_THREADS": ("ai", "cpu_threads", int),
    "AI_MODEL": ("ai", "model", str),
    "AI_DEVICE": ("ai", "device", str),
    "AI_TRACK_OBJECTS": ("ai", "tracked_objects", lambda s: [x.strip() for x in s.split(",") if x.strip()]),
    "AI_CONFIDENCE": ("ai", "confidence", float),
    "AI_RECORD_CONFIDENCE": ("ai", "record_confidence", float),
    "AI_IMAGE_SIZE": ("ai", "image_size", int),
    "AI_MAX_FPS": ("ai", "max_fps", float),
    "AI_CROP_MOTION": ("ai", "crop_motion", boolean),
    "AI_VALIDATION_WINDOW": ("validation", "window_frames", int),
    "AI_VALIDATION_MIN_POSITIVE": ("validation", "positive_frames", int),
    "AI_VALIDATION_MAX_GAP_SECONDS": ("validation", "max_gap_seconds", float),
    "AI_VALIDATION_INSTANT_CONFIDENCE": ("validation", "instant_confidence", float),
    "EVENT_COOLDOWN_SECONDS": ("events", "cooldown_seconds", float),
    "RETENTION_DAYS": ("storage", "retention_days", int),
    "MIN_FREE_DISK_MB": ("storage", "min_free_mb", int),
    "RECORD_PRE_ROLL_SECONDS": ("recording", "pre_roll_seconds", int),
    "RECORD_CLIP_SECONDS": ("recording", "clip_seconds", int),
    "RECORDING_SEGMENT_SECONDS": ("recording", "segment_seconds", int),
    "NOTIFICATION_COOLDOWN_SECONDS": ("notifications", "cooldown_seconds", float),
    "BACKUP_SCAN_SECONDS": ("backup", "scan_seconds", float),
    "BACKUP_TIMEOUT_SECONDS": ("backup", "timeout_seconds", int),
    "MONTAGE_ENABLED": ("montage", "enabled", boolean),
    "MONTAGE_INTERVAL_SECONDS": ("montage", "interval_seconds", float),
    "MONTAGE_MAX_FRAMES": ("montage", "max_frames", int),
    "MONTAGE_MAX_SECONDS": ("montage", "max_seconds", float),
    "MONTAGE_IDLE_SECONDS": ("montage", "idle_seconds", float),
    "WEB_ENABLED": ("web", "enabled", boolean),
    "WEB_HOST": ("web", "host", str),
    "WEB_PORT": ("web", "port", int),
    "LOG_LEVEL": ("logging", "level", str),
    "STATS_INTERVAL_SECONDS": ("logging", "stats_interval_seconds", float),
}


def load_config(path: str | Path = "config.yaml", environ: dict | None = None) -> dict:
    env = os.environ if environ is None else environ
    cfg = copy.deepcopy(DEFAULTS)
    config_path = Path(path).expanduser().resolve()
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError("Invalid YAML configuration") from exc
        if not isinstance(raw, dict):
            raise ConfigError("YAML configuration must be a mapping")
        for section, values in raw.items():
            if section not in cfg:
                raise ConfigError(f"Unknown section: {section}")
            if isinstance(cfg[section], dict):
                if not isinstance(values, dict) or set(values) - set(cfg[section]):
                    raise ConfigError(f"Invalid settings in section: {section}")
                cfg[section].update(values)
            else:
                cfg[section] = values
    for variable, (section, key, convert) in ENV_MAP.items():
        if env.get(variable, "").strip():
            try:
                cfg[section][key] = convert(env[variable])
            except (ValueError, TypeError) as exc:
                raise ConfigError(f"Invalid {variable}: expected {convert.__name__}") from exc
    cfg["camera"]["url"] = env.get("CAMERA_URL", "").strip()
    cfg["camera"]["recording_url"] = env.get("RECORDING_CAMERA_URL", "").strip() or cfg["camera"]["url"]
    if not cfg["camera"]["url"]:
        raise ConfigError("CAMERA_URL is required. Set a complete rtsp:// URL in .env or your environment.")
    if urlsplit(cfg["camera"]["url"]).scheme not in ("rtsp", "rtsps"):
        raise ConfigError("CAMERA_URL must use rtsp:// or rtsps://")
    if not urlsplit(cfg["camera"]["url"]).hostname:
        raise ConfigError("CAMERA_URL must have a hostname")
    parsed_recording = urlsplit(cfg["camera"]["recording_url"])
    if parsed_recording.scheme not in ("rtsp", "rtsps") or not parsed_recording.hostname:
        raise ConfigError("RECORDING_CAMERA_URL must be a complete RTSP URL")
    # Secrets and feature switches are environment-only, regardless of YAML values.
    for flag, section, key in [
        ("RECORD_EVENTS", "recording", "events"),
        ("CONTINUOUS_RECORDING", "recording", "continuous"),
        ("DELETE_AFTER_UPLOAD", "backup", "delete_after_upload"),
    ]:
        try:
            cfg[section][key] = boolean(env.get(flag, "false") or "false")
        except ConfigError as exc:
            raise ConfigError(f"Invalid {flag}: expected true or false") from exc
    notify = cfg["notifications"]
    notify.update(
        url=env.get("NTFY_URL", "").strip().rstrip("/"),
        topic=env.get("NTFY_TOPIC", "").strip(),
        token=env.get("NTFY_TOKEN", "").strip(),
    )
    notify["enabled"] = bool(notify["url"] and notify["topic"])
    if notify["enabled"]:
        if urlsplit(notify["url"]).scheme not in ("http", "https"):
            raise ConfigError("NTFY_URL must be an HTTP(S) URL")
        if not all(c.isalnum() or c in "_-" for c in notify["topic"]):
            raise ConfigError("NTFY_TOPIC must contain letters, digits, underscores or hyphens")
    backup = cfg["backup"]
    backup.update(remote=env.get("BACKUP_REMOTE", "").strip(), path=env.get("BACKUP_PATH", "").strip())
    try:
        requested = boolean(env.get("BACKUP_ENABLED", "false") or "false")
    except ConfigError as exc:
        raise ConfigError("Invalid BACKUP_ENABLED: expected true or false") from exc
    backup["enabled"] = bool(requested and backup["remote"] and backup["path"])
    if backup["enabled"] and (not backup["remote"].endswith(":") or backup["remote"].startswith("-")):
        raise ConfigError("BACKUP_REMOTE must be an rclone remote ending in ':' (e.g. storage:)")
    activity = cfg["activity"]
    activity["api_key"] = env.get("OPENROUTER_API_KEY", "").strip()
    activity["enabled"] = bool(activity["api_key"])
    if env.get("ACTIVITY_LLM_MODELS", "").strip():
        activity["models"] = [model.strip() for model in env["ACTIVITY_LLM_MODELS"].split(",") if model.strip()]
    if env.get("ACTIVITY_LLM_TIMEOUT_SECONDS", "").strip():
        try:
            activity["timeout_seconds"] = int(env["ACTIVITY_LLM_TIMEOUT_SECONDS"])
        except ValueError as exc:
            raise ConfigError("Invalid ACTIVITY_LLM_TIMEOUT_SECONDS: expected integer") from exc
    cfg["web"]["live_stream_url"] = env.get("LIVE_STREAM_URL", "").strip()
    if cfg["web"]["live_stream_url"] and urlsplit(cfg["web"]["live_stream_url"]).scheme not in ("http", "https"):
        raise ConfigError("LIVE_STREAM_URL must be an HTTP(S) browser player URL")
    cfg["data_dir"] = str(Path(env.get("DATA_DIR") or cfg["data_dir"]).expanduser().resolve())
    positive = {
        "camera": ["open_timeout_seconds", "read_timeout_seconds", "stale_seconds", "reconnect_max_seconds"],
        "motion": ["max_fps", "resize_width", "min_area", "threshold", "required_frames", "history"],
        "ai": ["image_size", "max_fps", "cpu_threads"],
        "validation": ["positive_frames", "window_frames", "max_gap_seconds"],
        "recording": ["clip_seconds", "segment_seconds"],
        "backup": ["scan_seconds", "timeout_seconds"],
        "activity": ["clip_seconds", "timeout_seconds"],
        "montage": ["interval_seconds", "max_frames", "max_seconds", "idle_seconds", "tile_width"],
        "logging": ["stats_interval_seconds"],
        "web": ["port"],
    }
    for section, keys in positive.items():
        for key in keys:
            value = cfg[section][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ConfigError(f"{section}.{key} must be a positive finite number")
    for section, keys in {
        "motion": ["warmup_frames", "cooldown_seconds", "crop_padding"],
        "events": ["cooldown_seconds"],
        "notifications": ["cooldown_seconds"],
    }.items():
        for key in keys:
            value = cfg[section][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ConfigError(f"{section}.{key} must be a nonnegative finite number")
    for section, keys in {
        "motion": ["resize_width", "warmup_frames", "required_frames", "history"],
        "ai": ["image_size", "cpu_threads"],
        "validation": ["positive_frames", "window_frames"],
        "recording": ["clip_seconds", "segment_seconds"],
        "activity": ["clip_seconds", "timeout_seconds"],
        "montage": ["max_frames", "tile_width"],
        "web": ["port"],
    }.items():
        if any(not isinstance(cfg[section][key], int) for key in keys):
            raise ConfigError(f"{section}: frame counts, sizes and ports must be integers")
    for section, keys in {
        "motion": ["enabled"],
        "ai": ["enabled", "crop_motion"],
        "montage": ["enabled"],
        "web": ["enabled"],
    }.items():
        if any(not isinstance(cfg[section][key], bool) for key in keys):
            raise ConfigError(f"{section}: boolean settings must be true/false")
    if not isinstance(cfg["camera"]["decoder"], str) or not re.fullmatch(r"[a-z0-9_]*", cfg["camera"]["decoder"]):
        raise ConfigError("CAMERA_DECODER must be an FFmpeg decoder name such as h264_v4l2m2m (empty = software)")
    if isinstance(cfg["camera"]["rotation"], bool) or cfg["camera"]["rotation"] not in (0, 90, 180, 270):
        raise ConfigError("CAMERA_ROTATION must be 0, 90, 180 or 270")
    if cfg["camera"]["transport"] not in ("tcp", "udp"):
        raise ConfigError("camera.transport must be tcp or udp")
    if cfg["ai"]["backend"] not in ("ultralytics", "ncnn"):
        raise ConfigError("AI_BACKEND must be ultralytics or ncnn")
    if cfg["montage"]["max_frames"] < 2:
        raise ConfigError("MONTAGE_MAX_FRAMES must be at least 2")
    if cfg["activity"]["clip_seconds"] != 10:
        raise ConfigError("activity.clip_seconds is fixed at 10 seconds")
    if not isinstance(activity["models"], list) or not activity["models"]:
        raise ConfigError("ACTIVITY_LLM_MODELS must contain at least one model")
    if any(not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]+", model) for model in activity["models"]):
        raise ConfigError("ACTIVITY_LLM_MODELS contains an invalid model identifier")
    if cfg["ai"]["image_size"] % 32 or cfg["web"]["port"] > 65535:
        raise ConfigError("AI image size must be a multiple of 32; port must be <=65535")
    if cfg["validation"]["positive_frames"] > cfg["validation"]["window_frames"]:
        raise ConfigError("AI_VALIDATION_MIN_POSITIVE must be <= AI_VALIDATION_WINDOW")
    instant = cfg["validation"]["instant_confidence"]
    if isinstance(instant, bool) or not isinstance(instant, (int, float)) or not 0 <= instant <= 1:
        raise ConfigError("AI_VALIDATION_INSTANT_CONFIDENCE must be 0 (off) or in (0,1]")
    if instant and instant < cfg["ai"]["confidence"]:
        raise ConfigError("AI_VALIDATION_INSTANT_CONFIDENCE must be 0 (off) or at least AI_CONFIDENCE")
    if cfg["validation"]["max_gap_seconds"] < 1 / cfg["ai"]["max_fps"]:
        raise ConfigError("AI_VALIDATION_MAX_GAP_SECONDS must be >= 1 / AI_MAX_FPS")
    thresholds = cfg["ai"]["confidence_by_class"]
    if not isinstance(thresholds, dict):
        raise ConfigError("ai.confidence_by_class must be a mapping")
    for value in [cfg["ai"]["confidence"], *thresholds.values()]:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
            raise ConfigError("AI confidence must be in (0,1]")
    record = cfg["ai"]["record_confidence"]
    if isinstance(record, bool) or not isinstance(record, (int, float)) or not 0 <= record < 1:
        raise ConfigError("AI_RECORD_CONFIDENCE must be 0 (off) or in (0,1)")
    lowest_alert = min([cfg["ai"]["confidence"], *thresholds.values()])
    if record and record >= lowest_alert:
        raise ConfigError("AI_RECORD_CONFIDENCE must be lower than AI_CONFIDENCE")
    objects = cfg["ai"]["tracked_objects"]
    if not isinstance(objects, list) or not objects or any(not isinstance(x, str) or not x for x in objects):
        raise ConfigError("AI_TRACK_OBJECTS must contain at least one class")
    if cfg["motion"]["learning_rate"] != -1 and not 0 <= cfg["motion"]["learning_rate"] <= 1:
        raise ConfigError("MOTION_LEARNING_RATE must be -1 or 0..1")
    if cfg["logging"]["level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError("Invalid LOG_LEVEL")
    if not isinstance(cfg["zones"], list):
        raise ConfigError("zones must be a list")
    for key in ("retention_days", "min_free_mb"):
        if type(cfg["storage"][key]) is not int or cfg["storage"][key] <= 0:
            raise ConfigError(f"storage.{key} must be a positive integer")
    if type(cfg["recording"]["pre_roll_seconds"]) is not int or not 0 <= cfg["recording"]["pre_roll_seconds"] <= 60:
        raise ConfigError("RECORD_PRE_ROLL_SECONDS must be an integer in 0..60")
    model = Path(cfg["ai"]["model"]).expanduser()
    if "/" in cfg["ai"]["model"] or (config_path.parent / model).exists():
        cfg["ai"]["model"] = str((config_path.parent / model).resolve())
    from .zones import ZoneManager

    try:
        ZoneManager(cfg["zones"])
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return cfg
