from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.config import load_config
from app.events import EventStore
from app.utils import Status
from app.web import create_app


@pytest.fixture
def catalog(tmp_path):
    store = EventStore(tmp_path)
    yield store
    store.close()


def event(store, identifier, stamp):
    store.add(
        {
            "id": identifier,
            "timestamp": stamp,
            "label": "person",
            "zone": "full_frame",
            "confidence": 0.9,
            "snapshot_path": f"events/{identifier}.jpg",
            "recording_status": "disabled",
        }
    )


def epoch(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def test_buckets_count_every_event_and_keep_newest_evidence(catalog):
    event(catalog, "before", "2026-10-03T23:59:59+00:00")
    event(catalog, "first", "2026-10-04T00:00:00+00:00")
    event(catalog, "newest", "2026-10-04T00:14:59+00:00")
    event(catalog, "next", "2026-10-04T00:15:00+00:00")
    event(catalog, "after", "2026-10-05T00:00:00+00:00")
    buckets = catalog.timeline(epoch("2026-10-04T00:00:00+00:00"), epoch("2026-10-05T00:00:00+00:00"))
    assert [(b["index"], b["count"], b["event"]["id"]) for b in buckets] == [(0, 2, "newest"), (1, 1, "next")]


def test_daily_range_includes_both_dst_hours(catalog):
    event(catalog, "early", "2026-10-25T02:10:00+02:00")
    event(catalog, "late", "2026-10-25T02:10:00+01:00")
    start, end = epoch("2026-10-25T00:00:00+02:00"), epoch("2026-10-26T00:00:00+01:00")
    assert end - start == 25 * 3600
    assert [(b["index"], b["count"]) for b in catalog.timeline(start, end)] == [(8, 1), (12, 1)]


def test_timeline_api_validation_empty_and_public_urls(catalog, tmp_path):
    cfg = load_config(tmp_path / "none", {"CAMERA_URL": "rtsp://example/live", "DATA_DIR": str(tmp_path)})
    rt = SimpleNamespace(status=Status(cfg), store=catalog, start=lambda: None, close=lambda: None)
    with TestClient(create_app(cfg, rt)) as client:
        assert client.get("/api/timeline?start=0&end=86400").json()["total"] == 0
        assert client.get("/api/timeline").status_code == 422
        for query in ("start=2&end=1", "start=0&end=0", "start=0&end=100000", "start=0&end=inf", "start=nan&end=1"):
            assert client.get(f"/api/timeline?{query}").status_code == 422
        event(catalog, "sample", "2026-10-04T08:00:00+00:00")
        start, end = epoch("2026-10-04T00:00:00+00:00"), epoch("2026-10-05T00:00:00+00:00")
        data = client.get(f"/api/timeline?start={start}&end={end}").json()
        assert data["total"] == 1 and data["labels"] == {"person": 1}
        assert data["buckets"][0]["event"]["snapshot_url"] == "/media/events/sample.jpg"
        assert data["bucket_seconds"] == 900
        font = client.get("/static/fonts/manrope-latin.woff2")
        assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"


def test_label_counts_cover_the_range_only(catalog):
    event(catalog, "yesterday", "2026-10-03T23:00:00+00:00")
    event(catalog, "today", "2026-10-04T08:00:00+00:00")
    catalog.add({**catalog.list_events(1)[0], "id": "car", "label": "car", "timestamp": "2026-10-04T09:00:00+00:00"})
    start, end = epoch("2026-10-04T00:00:00+00:00"), epoch("2026-10-05T00:00:00+00:00")
    assert catalog.label_counts(start, end) == {"person": 1, "car": 1}
    assert catalog.label_counts(start - 86400, start) == {"person": 1}
