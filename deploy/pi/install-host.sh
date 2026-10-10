#!/usr/bin/env bash
# Host-side setup of the camera stack on a Raspberry Pi: everything Docker Compose cannot do.
#
#   deploy/pi/install-host.sh            apply (idempotent: unchanged steps are only verified)
#   deploy/pi/install-host.sh --check    verify only, change nothing
#
# Run as the user that owns the repository and runs the camera; sudo is used where root is needed.
# Covers: video group, user lingering, camera-source (CSI host adapter), hardware watchdog,
# camera-watchdog recovery service, and keeping dhcpcd away from Docker's interfaces.
# The Docker stack itself is started separately with `docker compose up -d`.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy/pi/install-host.sh [--check] [--skip-camera-source] [--skip-watchdog] [--skip-network]
                                 [--skip-cpu-governor]

  --check               verify the host configuration without changing anything
  --skip-camera-source  do not install the CSI host adapter (test video or an external RTSP camera)
  --skip-watchdog       do not install the hardware watchdog and the camera-watchdog service
  --skip-network        do not touch /etc/dhcpcd.conf
  --skip-cpu-governor   keep the ondemand CPU governor (no fixed-clock service)
  -h, --help            show this help

Exit status: 0 when everything is configured and verified, 1 when something needs attention
(in --check mode: when anything would be changed).
EOF
}

CHECK=0 CAMERA_SOURCE=1 WATCHDOG=1 NETWORK=1 GOVERNOR=1
for argument in "$@"; do
  case "$argument" in
    --check) CHECK=1 ;;
    --skip-camera-source) CAMERA_SOURCE=0 ;;
    --skip-watchdog) WATCHDOG=0 ;;
    --skip-network) NETWORK=0 ;;
    --skip-cpu-governor) GOVERNOR=0 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/deploy/pi"
TARGET_USER="$(id -un)"
TARGET_UID="$(id -u)"
export XDG_RUNTIME_DIR="/run/user/$TARGET_UID"  # systemctl --user over SSH needs it
STAMP="$(date +%Y%m%d-%H%M%S)"
WARNINGS=0 CHANGES=0 RESTART_USER_MANAGER=0 SYSTEM_RELOAD=0

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
ok() { printf '  ok      %s\n' "$*"; }
warn() { printf '  WARN    %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
would() { printf '  todo    %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
did() { printf '  changed %s\n' "$*"; CHANGES=$((CHANGES + 1)); }
have() { command -v "$1" >/dev/null 2>&1; }
# Collect output before matching: with pipefail, `producer | grep -q` fails whenever grep exits early
# and the producer dies of SIGPIPE, so a found match could read as a miss depending on timing.
contains_line() { grep -Eq "$2" <<<"$1"; }
# A missing piece is a "todo" in --check mode and a warning after an apply run.
problem() { if [ "$CHECK" = 1 ]; then would "$@"; else warn "$@"; fi; }
# verify DESCRIPTION COMMAND...: ok when COMMAND succeeds, otherwise a problem.
verify() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then ok "$description"; else problem "not: $description"; fi
}

# install_file SOURCE DEST MODE NEEDS_SUDO: copies only when the content differs; returns 0 if changed.
install_file() {
  local source="$1" dest="$2" mode="$3" elevate="$4"
  if [ -f "$dest" ] && cmp -s "$source" "$dest"; then
    return 1
  fi
  if [ "$CHECK" = 1 ]; then
    would "$dest is missing or differs from the repository"
    return 1
  fi
  if [ "$elevate" = 1 ]; then
    [ -f "$dest" ] && sudo cp -p "$dest" "$dest.bak-$STAMP"
    sudo install -D -m "$mode" "$source" "$dest"
  else
    [ -f "$dest" ] && cp -p "$dest" "$dest.bak-$STAMP"
    install -D -m "$mode" "$source" "$dest"
  fi
  did "$dest"
  return 0
}

step "Preconditions"
if [ "$(id -u)" = 0 ]; then
  echo "Run this as the camera user (not root); it calls sudo where needed." >&2
  exit 1
fi
if [ "$(uname -s)" != Linux ]; then
  echo "This script configures a Raspberry Pi (Linux) host." >&2
  exit 1
fi
model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
case "$model" in
  *"Raspberry Pi"*) ok "$model" ;;
  *) warn "not a Raspberry Pi (${model:-unknown model}); continuing" ;;
esac
for tool in systemctl python3 docker; do
  if have "$tool"; then ok "$tool found"; else warn "$tool not found"; fi
done
if have python3 && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  warn "python3 is older than 3.9; the host scripts need 3.9+"
fi
if [ -f "$ROOT/.env" ]; then
  ok ".env present"
else
  warn ".env missing: copy .env.example and fill in CAMERA_URL etc."
fi
if [ "$CHECK" = 0 ]; then
  sudo -v  # ask for the password once, up front
fi

if [ "$CAMERA_SOURCE" = 1 ]; then
  step "Camera access (video group)"
  if contains_line "$(id -nG "$TARGET_USER" | tr ' ' '\n')" '^video$'; then
    ok "$TARGET_USER is in the video group"
  elif [ "$CHECK" = 1 ]; then
    would "add $TARGET_USER to the video group (libcamera sees no camera without it)"
  else
    sudo usermod -aG video "$TARGET_USER"
    did "$TARGET_USER added to the video group"
    RESTART_USER_MANAGER=1
  fi
  tool=""
  for name in rpicam-vid libcamera-vid; do
    have "$name" && { tool="$name"; break; }
  done
  # sg applies a membership added in this run; it would prompt for a group password if not a member yet.
  if contains_line "$(getent group video | cut -d: -f4 | tr ',' '\n')" "^${TARGET_USER}\$"; then
    list_cameras() { sg video -c "timeout 20 $1 --list-cameras" 2>&1; }
  else
    list_cameras() { timeout 20 "$1" --list-cameras 2>&1; }
  fi
  if [ -z "$tool" ]; then
    warn "neither rpicam-vid nor libcamera-vid is installed"
  elif contains_line "$(list_cameras "$tool" || true)" '^[[:space:]]*[0-9]+[[:space:]]*:'; then
    ok "$tool lists a camera"
  else
    warn "$tool lists no camera (CAMERA connector, cable orientation, power off before plugging)"
  fi

  step "User services start at boot (linger)"
  if [ "$(loginctl show-user "$TARGET_USER" -p Linger --value 2>/dev/null || echo no)" = yes ]; then
    ok "linger enabled"
  elif [ "$CHECK" = 1 ]; then
    would "enable linger so camera-source starts without a login"
  else
    sudo loginctl enable-linger "$TARGET_USER"
    did "linger enabled"
  fi

  step "Camera source (CSI host adapter, user service)"
  rendered="$(mktemp)"
  trap 'rm -f "$rendered" "${rendered_wd:-}"' EXIT
  if [ "$ROOT" = "$HOME/camera-stack" ]; then
    cp "$DEPLOY/camera-source.service" "$rendered"  # the unit's %h/camera-stack already points here
  else
    sed "s#%h/camera-stack#$ROOT#g" "$DEPLOY/camera-source.service" >"$rendered"
  fi
  if install_file "$rendered" "$HOME/.config/systemd/user/camera-source.service" 644 0; then
    systemctl --user daemon-reload
    CAMERA_SOURCE_CHANGED=1
  fi
  if [ "$CHECK" = 0 ]; then
    if ! systemctl --user is-enabled --quiet camera-source 2>/dev/null; then
      systemctl --user enable camera-source >/dev/null
      did "camera-source enabled"
    fi
    if [ "${CAMERA_SOURCE_CHANGED:-0}" = 1 ] && systemctl --user is-active --quiet camera-source; then
      systemctl --user restart camera-source
      did "camera-source restarted with the new unit"
    elif ! systemctl --user is-active --quiet camera-source; then
      systemctl --user start camera-source
      did "camera-source started"
    fi
  fi
  verify "camera-source active" systemctl --user is-active --quiet camera-source
fi

if [ "$WATCHDOG" = 1 ]; then
  step "Hardware watchdog (resets a hung system or a reboot stuck for 2 minutes)"
  if [ ! -e /dev/watchdog ]; then
    warn "/dev/watchdog not present; skipping the hardware watchdog"
  else
    if install_file "$DEPLOY/hardware-watchdog.conf" /etc/systemd/system.conf.d/camera-hardware-watchdog.conf 644 1; then
      sudo systemctl daemon-reexec
      did "systemd re-executed with the watchdog settings"
    fi
    runtime="$(systemctl show -p RuntimeWatchdogUSec --value)"
    case "$runtime" in
      0 | "" | infinity) problem "hardware watchdog not armed" ;;
      *) ok "hardware watchdog armed (runtime $runtime, reboot $(systemctl show -p RebootWatchdogUSec --value))" ;;
    esac
  fi

  step "camera-watchdog recovery service (root)"
  rendered_wd="$(mktemp)"
  trap 'rm -f "${rendered:-}" "$rendered_wd"' EXIT
  sed "s#@ROOT@#$ROOT#g" "$DEPLOY/camera-watchdog.service" >"$rendered_wd"
  watchdog_changed=0
  if install_file "$rendered_wd" /etc/systemd/system/camera-watchdog.service 644 1; then
    SYSTEM_RELOAD=1
    watchdog_changed=1
  fi
  if [ "$CHECK" = 0 ]; then
    [ "$SYSTEM_RELOAD" = 1 ] && sudo systemctl daemon-reload
    if ! systemctl is-enabled --quiet camera-watchdog 2>/dev/null; then
      sudo systemctl enable camera-watchdog >/dev/null 2>&1
      did "camera-watchdog enabled"
    fi
    if [ "$watchdog_changed" = 1 ] && systemctl is-active --quiet camera-watchdog; then
      sudo systemctl restart camera-watchdog
      did "camera-watchdog restarted with the new unit"
    elif ! systemctl is-active --quiet camera-watchdog; then
      sudo systemctl start camera-watchdog
      did "camera-watchdog started"
    fi
  fi
  verify "camera-watchdog active" systemctl is-active --quiet camera-watchdog
fi

if [ "$GOVERNOR" = 1 ]; then
  step "Fixed CPU clock (no firmware clock-change requests from the ondemand governor)"
  if install_file "$DEPLOY/cpu-performance.service" /etc/systemd/system/cpu-performance.service 644 1; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now cpu-performance >/dev/null 2>&1
    sudo systemctl restart cpu-performance
  elif [ "$CHECK" = 0 ] && ! systemctl is-enabled --quiet cpu-performance 2>/dev/null; then
    sudo systemctl enable --now cpu-performance >/dev/null 2>&1
    did "cpu-performance enabled"
  fi
  governor="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
  if [ "$governor" = performance ]; then ok "CPU governor: performance"; else problem "CPU governor is $governor"; fi
fi

if [ "$NETWORK" = 1 ]; then
  step "dhcpcd leaves Docker interfaces alone"
  if [ ! -f /etc/dhcpcd.conf ]; then
    ok "no dhcpcd (e.g. NetworkManager): nothing to do"
  elif grep -Eq '^[[:space:]]*denyinterfaces.*veth' /etc/dhcpcd.conf; then
    ok "denyinterfaces covers veth*"
  elif [ "$CHECK" = 1 ]; then
    would "add 'denyinterfaces veth* docker* br-*' to /etc/dhcpcd.conf"
  else
    sudo cp -p /etc/dhcpcd.conf "/etc/dhcpcd.conf.bak-$STAMP"
    printf '\n# Docker manages its own veth/bridge interfaces; IPv4LL routes on them disturb container networking.\ndenyinterfaces veth* docker* br-*\n' |
      sudo tee -a /etc/dhcpcd.conf >/dev/null
    did "/etc/dhcpcd.conf: denyinterfaces added (takes effect at the next boot or dhcpcd restart)"
  fi
fi

if [ "$RESTART_USER_MANAGER" = 1 ]; then
  step "Applying the new group to user services"
  # A fresh user manager picks up the video group; an immediate restart can race its cgroup cleanup.
  sudo systemctl restart "user@$TARGET_UID.service" || { sleep 2; sudo systemctl start "user@$TARGET_UID.service"; }
  did "user manager restarted (log in again for interactive shells)"
fi

step "Summary"
if curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  ok "app health endpoint answers"
else
  warn "app not answering on :8000 (start the stack with: docker compose up -d)"
fi
say "  changes: $CHANGES, warnings/todo: $WARNINGS$([ "$CHECK" = 1 ] && echo ' (check mode: nothing changed)')"
[ "$WARNINGS" = 0 ]
