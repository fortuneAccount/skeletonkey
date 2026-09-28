#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"
SCREEN="${VNC_SCREEN:-1280x800x24}"
VNC_PORT="${VNC_PORT:-5901}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

is_running() {
    kill -0 "$1" 2>/dev/null
}

start_process() {
    local pid_file="$1"
    shift

    if [[ -f "$pid_file" ]] && is_running "$(<"$pid_file")"; then
        return
    fi

    "$@" >/tmp/"$(basename "$pid_file" .pid)".log 2>&1 &
    echo "$!" >"$pid_file"
}

mkdir -p /tmp/skeletonkey-vnc

start_process /tmp/skeletonkey-vnc/xvfb.pid \
    Xvfb "$DISPLAY" -screen 0 "$SCREEN" -ac +extension GLX +render -noreset

start_process /tmp/skeletonkey-vnc/fluxbox.pid fluxbox
start_process /tmp/skeletonkey-vnc/x11vnc.pid \
    x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport "$VNC_PORT"
start_process /tmp/skeletonkey-vnc/websockify.pid \
    websockify --web=/usr/share/novnc "$NOVNC_PORT" "localhost:$VNC_PORT"

echo "VNC display is available at DISPLAY=$DISPLAY"
echo "Open noVNC on port $NOVNC_PORT."
