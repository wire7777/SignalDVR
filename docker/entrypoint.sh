#!/bin/sh
set -eu

PORT="${SIGNALDVR_PORT:-8088}"
WEB_THREADS="${SIGNALDVR_WEB_THREADS:-8}"
DATABASE_PATH="${SIGNALDVR_DATABASE:-/config/signaldvr.db}"

echo "[SignalDVR] Verifying persistent storage..."

for directory in \
  /config \
  /recordings \
  /timeshift \
  /thumbnails \
  /logs \
  /guide
do
  mkdir -p "$directory"

  test_file="$directory/.signaldvr-write-test"

  if ! touch "$test_file" 2>/dev/null; then
    echo "[SignalDVR] ERROR: $directory is not writable." >&2
    echo "[SignalDVR] Check the Docker volume path and host permissions." >&2
    exit 1
  fi

  rm -f "$test_file"
  echo "[SignalDVR]   OK: $directory"
done

database_directory="$(dirname "$DATABASE_PATH")"
mkdir -p "$database_directory"

database_test_file="$database_directory/.signaldvr-database-write-test"

if ! touch "$database_test_file" 2>/dev/null; then
  echo "[SignalDVR] ERROR: Database directory is not writable: $database_directory" >&2
  exit 1
fi

rm -f "$database_test_file"

echo "[SignalDVR] Verifying required media tools..."

command -v ffmpeg >/dev/null 2>&1 || {
  echo "[SignalDVR] ERROR: ffmpeg is missing." >&2
  exit 1
}

command -v ffprobe >/dev/null 2>&1 || {
  echo "[SignalDVR] ERROR: ffprobe is missing." >&2
  exit 1
}

echo "[SignalDVR] Verifying Python dependencies..."

python -c '
import flask
import requests
import sqlalchemy
import waitress
import werkzeug
' || {
  echo "[SignalDVR] ERROR: One or more required Python packages are missing." >&2
  exit 1
}

echo "[SignalDVR] Python dependencies OK."
echo "[SignalDVR] $(ffmpeg -version | head -n 1)"
echo "[SignalDVR] Database path: $DATABASE_PATH"
echo "[SignalDVR] Starting on port $PORT with $WEB_THREADS web threads"

exec waitress-serve \
  --host=0.0.0.0 \
  --port="$PORT" \
  --threads="$WEB_THREADS" \
  --connection-limit=200 \
  --channel-timeout=120 \
  app.app:app