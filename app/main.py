"""CLI entrypoint: python -m app.main; Uvicorn owns SIGINT/SIGTERM."""

import argparse
import logging
from pathlib import Path
import sys

from dotenv import load_dotenv
import uvicorn

from .config import ConfigError, load_config
from .utils import setup_logging
from .web import create_app


def main():
    parser = argparse.ArgumentParser(description="Motion-gated RTSP surveillance with a local web UI")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env-file", default=".env", help="Optional dotenv file; exported environment wins")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.check_config:
        print("Configuration OK (secrets hidden)")
        return 0
    setup_logging(Path(cfg["data_dir"]), cfg["logging"]["level"])
    try:
        uvicorn.run(
            create_app(cfg),
            host=cfg["web"]["host"],
            port=cfg["web"]["port"],
            log_level=cfg["logging"]["level"].lower(),
            access_log=False,
            timeout_graceful_shutdown=60,
        )
    except Exception as exc:
        logging.getLogger(__name__).error("application failed (%s)", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
