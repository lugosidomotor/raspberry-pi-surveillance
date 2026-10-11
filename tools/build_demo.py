"""Build the standalone demo page: the unchanged web UI (app/static) plus an in-browser mock backend.

The result is one self-contained HTML file that opens straight from disk, without the app or a Pi:
    python tools/build_demo.py            # writes demo/camera-stack-demo.html
    python tools/build_demo.py --check    # exits 1 when the committed demo is out of date
"""

import argparse
import base64
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
SOURCES = ROOT / "tools" / "demo"
OUTPUT = ROOT / "demo" / "camera-stack-demo.html"


def replace_once(text: str, old: str, new: str, where: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{where}: expected exactly one {old!r}; update tools/build_demo.py to match the UI")
    return text.replace(old, new)


def inline_script(code: str, where: str) -> str:
    if "</script" in code.lower() or "<!--" in code:
        raise SystemExit(f"{where}: cannot be inlined (contains </script or <!--)")
    return f"<script>\n{code}</script>\n"


def inline_assets(css: str) -> str:
    """Embed url(/static/...) assets (the self-hosted fonts) as data URIs."""
    types = {".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png"}

    def embed(match):
        path = STATIC / match.group(1)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'url("data:{types[path.suffix]};base64,{data}")'

    return re.sub(r"""url\(["']?/static/([^"')]+)["']?\)""", embed, css)


def build() -> str:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    icon = base64.b64encode((STATIC / "camera.svg").read_bytes()).decode("ascii")
    styles = inline_assets((STATIC / "style.css").read_text(encoding="utf-8"))
    styles += (SOURCES / "demo.css").read_text(encoding="utf-8")
    # A file:// page has no server routes: the events view becomes ?page=events.
    app = replace_once(
        app,
        'const isEvents = location.pathname === "/events";',
        'const isEvents = new URLSearchParams(location.search).get("page") === "events";',
        "app.js",
    )
    page = replace_once(page, "<title>Camera Stack</title>", "<title>Camera Stack · Demó</title>", "index.html")
    page = replace_once(
        page,
        '<link rel="icon" href="/static/camera.svg" type="image/svg+xml">',
        f'<link rel="icon" href="data:image/svg+xml;base64,{icon}">',
        "index.html",
    )
    page = replace_once(page, '<link rel="stylesheet" href="/static/style.css">', f"<style>\n{styles}</style>", "index")
    page = replace_once(page, '  <script defer src="/static/app.js"></script>\n', "", "index.html")
    page = page.replace('href="/events"', 'href="?page=events"').replace('href="/"', 'href="?"')
    scripts = inline_script((SOURCES / "mock.js").read_text(encoding="utf-8"), "mock.js") + inline_script(app, "app.js")
    return replace_once(page, "</body>", scripts + "</body>", "index.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the output differs from a fresh build")
    args = parser.parse_args()
    html = build()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != html:
            sys.exit(f"{args.output} is out of date; run: python tools/build_demo.py")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"wrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")


if __name__ == "__main__":
    main()
