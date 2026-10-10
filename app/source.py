"""Public source identity, without disclosing camera URLs or local video paths."""

import json
from pathlib import Path


def source_identity(cfg):
    if cfg["camera"].get("test_stream", False):
        return {"mode": "test", "is_test": True}
    try:
        record = json.loads((Path(cfg["data_dir"]) / "source-status.json").read_text())
        mode = record.get("mode")
        if mode in ("test", "camera"):
            return {"mode": mode, "is_test": mode == "test", "publisher": record.get("state", "unknown")}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"mode": "camera", "is_test": False}
