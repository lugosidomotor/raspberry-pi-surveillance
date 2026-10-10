# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS base
ARG TARGETARCH
ARG APP_UID=1000
ARG APP_GID=1000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data YOLO_CONFIG_DIR=/data/.ultralytics HOME=/data \
    RCLONE_CONFIG=/data/rclone/rclone.conf
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg rclone libgl1 libglib2.0-0 libgomp1 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid "$APP_GID" camera \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --home-dir /data camera \
    && mkdir -p /data && chown -R camera:camera /data /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('WEB_PORT','8000')+'/health',timeout=4)" || exit 1
CMD ["python", "-m", "app.main", "--config", "/app/config.yaml"]

FROM base AS pytorch-deps
ENV AI_MODEL=/opt/models/yolo11n.pt
COPY requirements-pytorch.lock ./
# Explicit CPU wheels on both architectures; PyPI ARM64 can include large CUDA dependencies.
RUN python -m pip install --no-deps -r requirements-pytorch.lock --extra-index-url https://download.pytorch.org/whl/cpu \
    && python -m pip uninstall -y opencv-python \
    && python -m pip install --force-reinstall --no-deps 'opencv-python-headless==4.14.0.94'
# Bundle the preferred model so runtime does not depend on first-run downloads.
RUN mkdir -p /opt/models \
    && python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" \
    && mv yolo11n.pt /opt/models/yolo11n.pt
# Build only the media features used by capture, clips, pre-roll and test preparation.
# No compiler, GUI stack or distro-wide FFmpeg dependency graph enters the runtime.
FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS media-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config nasm curl gnupg ca-certificates libx264-dev libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 180 --speed-time 30 --speed-limit 1024 -o ffmpeg.tar.xz https://ffmpeg.org/releases/ffmpeg-8.0.3.tar.xz \
    && curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 180 -o ffmpeg.tar.xz.asc https://ffmpeg.org/releases/ffmpeg-8.0.3.tar.xz.asc \
    && curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 180 -o key.asc https://ffmpeg.org/ffmpeg-devel.asc \
    && gpg --import key.asc \
    && gpg --with-colons --fingerprint B4322F04D67658D8 | grep -q 'FCF986EA15E6E293A5644F10B4322F04D67658D8' \
    && gpg --verify ffmpeg.tar.xz.asc ffmpeg.tar.xz \
    && tar -xf ffmpeg.tar.xz --strip-components=1
RUN ./configure --prefix=/opt/media --disable-shared --enable-static \
      --disable-autodetect --disable-everything --disable-doc --disable-debug --disable-ffplay --enable-small \
      --enable-ffmpeg --enable-ffprobe --enable-network --enable-gpl --enable-version3 --enable-libx264 --enable-openssl --enable-zlib \
      --enable-protocol=file,pipe,tcp,udp,rtp,http,https,tls,crypto,httpproxy \
      --enable-demuxer=mov,matroska,h264,hevc,rtsp,rtp,sdp,concat,image2,image2pipe,mpegts,aac,mp3 \
      --enable-muxer=mp4,mov,matroska,rtsp,rtp,segment,stream_segment,image2,image2pipe,null,mpegts \
      --enable-decoder=h264,hevc,mjpeg,png,rawvideo,wrapped_avframe,aac,mp3,opus,vp8,vp9,av1 \
      --enable-parser=h264,hevc,aac,mpegaudio,opus,vp8,vp9,av1 \
      --enable-encoder=libx264,png,mjpeg,wrapped_avframe,rawvideo,aac \
      --enable-filter=scale,fps,format,reverse,color,null,anull,aresample,trim,atrim,setpts,asetpts --enable-indev=lavfi \
      --enable-bsf=h264_mp4toannexb,hevc_mp4toannexb,aac_adtstoasc,extract_extradata,filter_units,null \
    && make -j4 && make install

FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS lean-base
ARG APP_UID=1000
ARG APP_GID=1000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data YOLO_CONFIG_DIR=/data/.ultralytics HOME=/data \
    RCLONE_CONFIG=/data/rclone/rclone.conf
WORKDIR /app
# headless OpenCV does not need libGL, GTK, Qt or libglib.
RUN apt-get update && apt-get install -y --no-install-recommends \
    rclone libx264-164 libssl3 zlib1g libgomp1 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$APP_GID" camera \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --home-dir /data camera \
    && mkdir -p /data && chown camera:camera /data /app
COPY --from=media-build /opt/media/bin/ffmpeg /opt/media/bin/ffprobe /usr/local/bin/
COPY --from=media-build /build/COPYING.GPLv3 /usr/share/doc/camera-stack/FFmpeg-COPYING.GPLv3
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('WEB_PORT','8000')+'/health',timeout=4)" || exit 1
CMD ["python", "-m", "app.main", "--config", "/app/config.yaml"]

FROM lean-base AS runtime
COPY --from=pytorch-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=pytorch-deps /opt/models/yolo11n.pt /opt/models/yolo11n.pt
ENV AI_MODEL=/opt/models/yolo11n.pt
COPY --chown=camera:camera app ./app
COPY --chown=camera:camera config.yaml ./config.yaml
USER camera

# Optional Pi-optimized image. Export is bundled at build time, never at startup.
FROM pytorch-deps AS ncnn-export
USER root
RUN python -m pip install --no-deps 'ncnn==1.0.20260526' 'pnnx==20260526' \
    && python -m pip install --no-deps 'tqdm==4.70.1' 'portalocker==4.4.0' \
    && python -c "from ultralytics import YOLO; YOLO('/opt/models/yolo11n.pt').export(format='ncnn', imgsz=320, device='cpu')"
FROM lean-base AS ncnn
COPY requirements-common.lock ./
RUN python -m pip install --no-deps -r requirements-common.lock \
    && python -m pip install --no-deps 'ncnn==1.0.20260526'
COPY --from=ncnn-export /opt/models/yolo11n_ncnn_model /opt/models/yolo11n_ncnn_model
COPY --chown=camera:camera app ./app
COPY --chown=camera:camera config.yaml ./config.yaml
ENV AI_BACKEND=ncnn AI_MODEL=/opt/models/yolo11n_ncnn_model
USER camera

# Isolated test image; use the same small media/runtime stack as production.
FROM ncnn AS test
USER root
COPY requirements-dev.lock ./
RUN python -m pip install --no-deps -r requirements-dev.lock
COPY --chown=camera:camera app ./app
COPY --chown=camera:camera config.yaml ./config.yaml
COPY --chown=camera:camera tests ./tests
COPY --chown=camera:camera deploy ./deploy
COPY --chown=camera:camera tools ./tools
COPY --chown=camera:camera pyproject.toml ./pyproject.toml
COPY --chown=camera:camera docker-compose*.yml ./
USER camera
HEALTHCHECK NONE
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# Default Docker workflow is Torch-free on both ARM64 and AMD64.
# Explicit --target runtime retains the optional PyTorch backend.
FROM ncnn AS production
