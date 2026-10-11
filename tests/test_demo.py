from tools import build_demo


def test_demo_inlines_the_real_ui_with_the_mock_backend_first():
    html = build_demo.build()
    app = (build_demo.STATIC / "app.js").read_text(encoding="utf-8")
    assert 'src="/static/' not in html and 'href="/static/' not in html  # self-contained: opens from disk
    assert "/static/fonts/" not in html and "data:font/woff2;base64," in html
    assert 'href="/events"' not in html and 'href="?page=events"' in html and 'href="?page=tablet"' in html
    assert app.splitlines()[-1] in html  # the unchanged UI code, not a copy that can drift
    mock, ui = (
        html.index("window.fetch = async"),
        html.index('const page = new URLSearchParams(location.search).get("page")'),
    )
    assert mock < ui  # the fake API is in place before the UI's first request


def test_demo_build_fails_loudly_when_the_ui_changes_shape(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    for name in ("app.js", "style.css", "camera.svg"):
        (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(build_demo, "STATIC", tmp_path)
    try:
        build_demo.build()
    except SystemExit as exc:
        assert "update tools/build_demo.py" in str(exc)
    else:
        raise AssertionError("a stale build script must not produce a broken demo silently")
