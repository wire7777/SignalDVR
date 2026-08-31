#!/usr/bin/env bash
set -e

echo "========================================"
echo " SignalDVR Docker Installer"
echo "========================================"

if ! command -v docker >/dev/null 2>&1; then
    echo
    echo "ERROR: Docker Engine is not installed."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo
    echo "ERROR: Docker Compose is not installed."
    exit 1
fi

echo
echo "Creating .env..."

[ -f .env ] || cp .env.example .env

echo
echo "Creating persistent folders..."

mkdir -p \
    data/config \
    data/recordings \
    data/timeshift \
    data/thumbnails \
    data/logs \
    data/guide

chmod -R u+rwX data

echo
echo "Building SignalDVR..."

docker compose up -d --build

echo
echo "========================================"
echo " SignalDVR Installed"
echo "========================================"
echo
echo "Open:"
echo
echo "    http://SERVER-IP:8088"
echo
echo "A new installation will automatically"
echo "redirect to the setup wizard."
echo
