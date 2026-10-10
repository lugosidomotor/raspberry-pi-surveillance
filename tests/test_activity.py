import json
import threading
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.activity import ActivityAnalyzer, parse_result
from app.activity import requests as activity_requests
from app.config import load_config
from app.events import EventStore
from app.utils import Status

ANSWER = (
    '{"verdict":"not_suspicious","activity":"Egy személy átsétál a kapu előtt.",'
    '"evidence":"Megáll, majd továbbmegy.","uncertainty":"none"}'
)


@pytest.fixture
def cfg(tmp_path):
    return load_config(
        tmp_path / "none",
        {
            "CAMERA_URL": "rtsp://example/live",
            "DATA_DIR": str(tmp_path),
            "OPENROUTER_API_KEY": "test-key",
            "ACTIVITY_LLM_MODELS": "first/model,second/model",
        },
    )


@pytest.fixture
def store(tmp_path):
    result = EventStore(tmp_path)
    yield result
    result.close()


def reply(content, finish_reason="stop", status=200, chunks=None):
    response = MagicMock(ok=status < 400, status_code=status)
    if status >= 400:
        body = b'{"error":{"message":"Rate limit exceeded"}}'
    else:
        body = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}).encode()
    # OpenRouter may pad a slow non-streaming answer with whitespace before the JSON body.
    response.iter_content.return_value = chunks if chunks is not None else [b"\n ", body]
    response.__enter__.return_value = response
    return response


@pytest.mark.parametrize(
    "content",
    [
        ANSWER,
        f"```json\n{ANSWER}\n```",
        f"<think>A {{verdict}} mező alapján...</think>\nVálasz: {ANSWER}",
        ANSWER.replace('"not_suspicious"', '"Not Suspicious"'),
    ],
)
def test_parse_result_tolerates_common_model_formatting(content):
    assert parse_result(content) == (
        "Nem gyanús",
        "Egy személy átsétál a kapu előtt.",
        "Megáll, majd továbbmegy.",
        "none",
    )


def test_parse_result_normalizes_uncertainty_and_long_text():
    verdict, activity_text, _, uncertainty = parse_result(
        '{"verdict":"suspicious","activity":"' + "a" * 400 + '","evidence":"Feszegeti a zárat.","uncertainty":"Low"}'
    )
    assert verdict == "Gyanús" and len(activity_text) == 300 and uncertainty == "some"


@pytest.mark.parametrize(
    "content", [None, "", '{"verdict":"suspicious or not_suspicious","activity":"abc","evidence":"abc"}', '{"verdict":']
)
def test_parse_result_rejects_unusable_answers(content):
    with pytest.raises(ValueError):
        parse_result(content)


def test_request_budget_leaves_room_for_reasoning(cfg, store):
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    with patch("app.activity.requests.post", return_value=reply(ANSWER)) as post:
        assert analyzer._request("first/model", "data:video/mp4;base64,AA==")[0] == "Nem gyanús"
    body = post.call_args.kwargs["json"]
    assert body["max_tokens"] >= 1024
    assert body["reasoning"] == {"effort": "none", "exclude": True}


def test_request_enforces_wall_clock_deadline_despite_keepalive_bytes(cfg, store):
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    clock = iter(range(0, 10_000, 10))
    with (
        patch("app.activity.requests.post", return_value=reply(None, chunks=iter(lambda: b" ", None))),
        patch("app.activity.time.monotonic", lambda: next(clock)),
    ):
        with pytest.raises(activity_requests.Timeout, match="within 75s"):
            analyzer._request("first/model", "data:video/mp4;base64,AA==")


def test_request_reports_exhausted_reasoning_budget(cfg, store):
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    with patch("app.activity.requests.post", return_value=reply("", finish_reason="length")):
        with pytest.raises(ValueError, match="token limit"):
            analyzer._request("first/model", "data:video/mp4;base64,AA==")


def test_http_error_detail_is_logged_and_next_model_used(cfg, store, tmp_path, caplog):
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    event = {"id": "e1", "timestamp": "2026-10-04T10:00:00.000+00:00", "label": "person", "zone": "full_frame"}
    store.add(event)
    clip = tmp_path / "events/2026-10-04/e1_activity.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"mp4")
    with patch("app.activity.requests.post", side_effect=[reply(None, status=429), reply(ANSWER)]):
        analyzer._process({**event, "activity_clip_path": "events/2026-10-04/e1_activity.mp4"})
    saved = store.list_events()[0]
    assert saved["activity_status"] == "completed" and saved["activity_model"] == "second/model"
    assert saved["activity_summary"].startswith("Nem gyanús — Egy személy")
    assert "HTTP 429" in caplog.text and "Rate limit exceeded" in caplog.text
    assert "test-key" not in caplog.text


def test_slow_analysis_does_not_delay_next_clip_capture(cfg, store, monkeypatch):
    release, captured = threading.Event(), []
    second_captured = threading.Event()

    def capture(self, event, destination):
        captured.append(event["id"])
        if len(captured) == 2:
            second_captured.set()

    monkeypatch.setattr(ActivityAnalyzer, "_capture", capture)
    monkeypatch.setattr(ActivityAnalyzer, "_process", lambda self, event: release.wait(5))
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    analyzer.start()
    try:
        for index in (1, 2):
            event = {"id": f"e{index}", "timestamp": "2026-10-04T10:00:00.000+00:00", "label": "person", "zone": "z"}
            store.add(event)
            analyzer.submit(event)
        # The first analysis is still blocked, yet the second scene is recorded right away.
        assert second_captured.wait(2) and captured == ["e1", "e2"]
    finally:
        release.set()
        assert analyzer.close(10)


def test_capture_failure_marks_event_failed(cfg, store, monkeypatch):
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    event = {"id": "e1", "timestamp": "2026-10-04T10:00:00.000+00:00", "label": "person", "zone": "z"}
    store.add(event)
    monkeypatch.setattr(analyzer, "_capture", Mock(side_effect=RuntimeError("ffmpeg")))
    analyzer.worker.submit = Mock()
    analyzer._record({**event, "activity_clip_path": "events/2026-10-04/e1_activity.mp4"})
    assert store.list_events()[0]["activity_status"] == "failed"
    analyzer.worker.submit.assert_not_called()


def test_notification_error_does_not_reanalyze_with_next_model(cfg, store, tmp_path):
    notifier = Mock()
    notifier.send_activity.side_effect = ValueError("bad header")
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg), notifier)
    event = {"id": "e1", "timestamp": "2026-10-04T10:00:00.000+00:00", "label": "person", "zone": "z"}
    store.add(event)
    clip = tmp_path / "events/2026-10-04/e1_activity.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"mp4")
    with patch("app.activity.requests.post", return_value=reply(ANSWER)) as post:
        with pytest.raises(ValueError):
            analyzer._process({**event, "activity_clip_path": "events/2026-10-04/e1_activity.mp4"})
    post.assert_called_once()
    assert store.list_events()[0]["activity_status"] == "completed"


def test_restart_settles_analyses_left_in_progress(cfg, store):
    for index, state in enumerate(("queued", "recording", "analyzing", "completed")):
        store.add(
            {
                "id": f"e{index}",
                "timestamp": f"2026-10-04T10:00:0{index}.000+00:00",
                "label": "person",
                "zone": "z",
                "activity_status": state,
                "activity_notification_status": "queued",
            }
        )
    analyzer = ActivityAnalyzer(cfg, store, Status(cfg))
    analyzer.start()
    try:
        states = {e["id"]: (e["activity_status"], e["activity_notification_status"]) for e in store.list_events()}
    finally:
        analyzer.close(2)
    assert states == {
        "e0": ("failed", "disabled"),
        "e1": ("failed", "disabled"),
        "e2": ("failed", "disabled"),
        "e3": ("completed", "queued"),
    }
