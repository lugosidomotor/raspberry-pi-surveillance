# Camera Stack
<!-- impeccable:product-schema 1 -->
## Platform
web
## Stack
User-specified Python 3.11, FastAPI, plain static web UI, Docker ARM64/AMD64, OpenCV and YOLO11n.
## Users
The owner monitors a home camera from a Mac initially and later a Raspberry Pi via Tailscale.
## Product Purpose
Use cheap motion detection to gate AI and retain evidence of temporally validated events.
## Operating Context
Private host-local web UI; host publishes RTSP and manages Tailscale. Optional ntfy and rclone.
## Capabilities and Constraints
Live player URL is optional and independent of RTSP. Missing integrations are disabled.
Camera URL is mandatory. No mock detections or invented surveillance history in shipped UI.
## Product Principles
Keep implementation small; show actual state; preserve recordings; keep optional services independent.
