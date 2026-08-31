#!/usr/bin/env bash
set -euo pipefail

echo "======================================"
echo " SignalDVR Native Ubuntu Installer"
echo "======================================"
echo

if [ "$(id -u)" -eq 0 ]; then
    echo "Please run this installer as your normal user, not root."
    exit 1
fi

SIGNALDVR_USER="${SUDO_USER:-$USER}"

REQUIRED_PACKAGES=(
    python3
    python3-venv
    python3-pip
    ffmpeg
    curl
    ca-certificates
    tzdata
    sqlite3
    hdhomerun-config
    dvb-tools
    w-scan-cpp
)

MISSING_PACKAGES=()

echo "Checking SignalDVR system dependencies..."
echo

for package in "${REQUIRED_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | \
        grep -q "install ok installed"; then
        echo "  OK: $package"
    else
        echo "  MISSING: $package"
        MISSING_PACKAGES+=("$package")
    fi
done

echo

if [ "${#MISSING_PACKAGES[@]}" -gt 0 ]; then
    echo "Installing missing packages:"
    printf '  %s\n' "${MISSING_PACKAGES[@]}"
    echo

    sudo apt-get update
    sudo apt-get install -y "${MISSING_PACKAGES[@]}"

    echo
    echo "Missing packages installed."
else
    echo "All required system packages are already installed."
    echo "Skipping apt update/install."
fi

echo

if getent group video >/dev/null 2>&1; then
    if id -nG "$SIGNALDVR_USER" | tr ' ' '\n' | grep -qx video; then
        echo "$SIGNALDVR_USER already has DVB/video device access."
    else
        echo "Adding $SIGNALDVR_USER to the video group..."
        sudo usermod -aG video "$SIGNALDVR_USER"
        echo "Video group access added."
        echo "A logout/login or reboot may be required before DVB devices are accessible."
    fi
fi

echo
echo "Checking required runtime tools..."

TOOLS=(
    ffmpeg
    dvbv5-scan
    dvbv5-zap
    w_scan_cpp
)

FAILED=0

for tool in "${TOOLS[@]}"; do
    if command -v "$tool" >/dev/null 2>&1; then
        echo "  OK: $tool -> $(command -v "$tool")"
    else
        echo "  MISSING: $tool"
        FAILED=1
    fi
done

echo

if [ -d /dev/dvb ]; then
    DVB_COUNT=$(
        find /dev/dvb \
            -maxdepth 2 \
            -name frontend0 \
            2>/dev/null \
            | wc -l
    )

    echo "Native DVB hardware detected: ${DVB_COUNT} tuner(s)"
else
    echo "No Native DVB hardware detected."
    echo "This is normal for HDHomeRun-only installations."
fi

echo

if [ "$FAILED" -ne 0 ]; then
    echo "One or more required SignalDVR runtime tools are missing."
    exit 1
fi

echo "SignalDVR native dependencies are ready."
