#!/usr/bin/env bash
set -euo pipefail

echo "Installing Qt6 and VNC dependencies..."

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    fluxbox \
    libegl1 \
    libxcb-cursor0 \
    libxcb-xtest0 \
    libxcb-xinput0 \
    libxcb-glx0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-shm0 \
    libxcb-sync1 \
    libxcb-xinerama0 \
    libxcb-xfixes0 \
    libxcb-xkb1 \
    libx11-xcb1 \
    libxkbcommon-x11-0 \
    novnc \
    websockify \
    x11vnc \
    xvfb

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r ../../../workspaces/freeduction/requirements.txt

echo "VNC dependencies and PyQt6 requirements are ready."
