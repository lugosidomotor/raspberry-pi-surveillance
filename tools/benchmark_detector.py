"""Compare detectors on the same decoded frame without generating events."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import load_config
from app.detector import create_detector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--backend", choices=["ultralytics", "ncnn"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    cap = cv2.VideoCapture(str(args.video))
    cap.set(cv2.CAP_PROP_POS_MSEC, args.seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        parser.error("Cannot decode requested frame")
    if args.crop:
        x1, y1, x2, y2 = args.crop
        if not (0 <= x1 < x2 <= frame.shape[1] and 0 <= y1 < y2 <= frame.shape[0]):
            parser.error("Crop must be inside the decoded frame")
        frame = frame[y1:y2, x1:x2]
    cfg = load_config(
        "/nonexistent.yaml",
        {"CAMERA_URL": "rtsp://example/cam", "AI_BACKEND": args.backend, "AI_MODEL": args.model, "AI_DEVICE": "cpu"},
    )["ai"]
    detector = create_detector(cfg)
    detector.detect(frame)  # Cold initialization excluded from steady-state measurement.
    times = []
    for _ in range(args.iterations):
        start = time.perf_counter()
        detections = detector.detect(frame)
        times.append((time.perf_counter() - start) * 1000)
    print(
        json.dumps(
            {
                "backend": args.backend,
                "iterations": len(times),
                "median_ms": round(statistics.median(times), 1),
                "mean_ms": round(statistics.mean(times), 1),
                "samples_ms": [round(t, 1) for t in times],
                "detections": [{"label": d.label, "confidence": round(d.confidence, 4)} for d in detections],
            }
        )
    )


if __name__ == "__main__":
    main()
