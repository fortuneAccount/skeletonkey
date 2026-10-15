#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"
SCREEN="${VNC_SCREEN:-1280x800x24}"
VNC_PORT="${VNC_PORT:-5901}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

is_running() {
    [[ "$1" =~ ^[0-9]+$ ]] && kill -0 "$1" 2>/dev/null
}

start_process() {
    local pid_file="$1"
    local log_file
    shift

    if [[ -f "$pid_file" ]] && is_running "$(<"$pid_file")"; then
        return
    fi

    log_file="/tmp/$(basename "$pid_file" .pid).log"
    "$@" >"$log_file" 2>&1 &
    echo "$!" >"$pid_file"
}

wait_for_socket() {
    local pid_file="$1"
    local label="$2"
    local port="$3"
    local log_file="/tmp/$(basename "$pid_file" .pid).log"
    local pid

    for _ in {1..75}; do
        pid="$(<"$pid_file")"
        if ! is_running "$pid"; then
            break
        fi
        if ( : >"/dev/tcp/127.0.0.1/$port" ) 2>/dev/null; then
            return
        fi
        sleep 0.2
    done

    echo "ERROR: $label did not listen on port $port; see $log_file." >&2
    if [[ -f "$log_file" ]]; then
        tail -40 "$log_file" >&2
    fi
    return 1
}

wait_for_x_server() {
    local pid_file="$1"
    local display_number="${DISPLAY##*:}"
    local socket_path

    display_number="${display_number%%.*}"
    socket_path="/tmp/.X11-unix/X${display_number}"
    for _ in {1..75}; do
        if ! is_running "$(<"$pid_file")"; then
            break
        fi
        [[ -S "$socket_path" ]] && return
        sleep 0.2
    done

    echo "ERROR: Xvfb did not create the display socket $socket_path." >&2
    if [[ -f /tmp/xvfb.log ]]; then
        tail -40 /tmp/xvfb.log >&2
    fi
    return 1
}

mkdir -p /tmp/skeletonkey-vnc

start_process /tmp/skeletonkey-vnc/xvfb.pid \
    Xvfb "$DISPLAY" -screen 0 "$SCREEN" -ac +extension GLX +render -noreset
wait_for_x_server /tmp/skeletonkey-vnc/xvfb.pid

start_process /tmp/skeletonkey-vnc/fluxbox.pid fluxbox
start_process /tmp/skeletonkey-vnc/x11vnc.pid \
    x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport "$VNC_PORT"
wait_for_socket /tmp/skeletonkey-vnc/x11vnc.pid x11vnc "$VNC_PORT"
start_process /tmp/skeletonkey-vnc/websockify.pid \
    websockify --web=/usr/share/novnc "$NOVNC_PORT" "localhost:$VNC_PORT"
wait_for_socket /tmp/skeletonkey-vnc/websockify.pid websockify "$NOVNC_PORT"

echo "VNC display is available at DISPLAY=$DISPLAY"
echo "Open noVNC on port $NOVNC_PORT."
