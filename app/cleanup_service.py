import time
import threading
from pathlib import Path

from app import config


CHECK_INTERVAL = 60 * 60       # run every hour
MAX_AGE_SECONDS = 60 * 60      # delete temp files older than 1 hour

_running = False
_thread = None


def _is_old(path: Path, max_age_seconds: int = MAX_AGE_SECONDS) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        return age > max_age_seconds
    except FileNotFoundError:
        return False


def _safe_delete(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except Exception as e:
        print("Cleanup delete error:", path, e, flush=True)

    return False


def cleanup_livebuffer():
    removed = 0

    patterns = [
        "*.ts",
        "*.m3u8",
        "*.log",
        "*.tmp",
        "*.part",
    ]

    for pattern in patterns:
        for path in config.LIVEBUFFER.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def cleanup_runtime_files():
    removed = 0
    base = config.BASE

    patterns = [
        "*.pid",
        "*_current.txt",
        "*_position.txt",
        "*_channel.txt",
    ]

    for pattern in patterns:
        for path in base.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def cleanup_temp_thumbnails():
    removed = 0

    patterns = [
        "tmp*",
        "*.tmp",
        "*.part",
    ]

    for pattern in patterns:
        for path in config.THUMBNAILS.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def run_cleanup_once():
    livebuffer_removed = cleanup_livebuffer()
    runtime_removed = cleanup_runtime_files()
    thumb_removed = cleanup_temp_thumbnails()

    total = livebuffer_removed + runtime_removed + thumb_removed

    print(
        "Cleanup complete:",
        f"livebuffer={livebuffer_removed}",
        f"runtime={runtime_removed}",
        f"thumbnails={thumb_removed}",
        f"total={total}",
        flush=True,
    )

    return {
        "livebuffer": livebuffer_removed,
        "runtime": runtime_removed,
        "thumbnails": thumb_removed,
        "total": total,
    }


def cleanup_loop():
    global _running

    print("SignalDVR cleanup service started", flush=True)

    while _running:
        try:
            run_cleanup_once()
        except Exception as e:
            print("Cleanup service error:", e, flush=True)

        time.sleep(CHECK_INTERVAL)


def start_cleanup_service():
    global _running, _thread

    if _running:
        print("SignalDVR cleanup service already running", flush=True)
        return

    print("Starting SignalDVR cleanup service...", flush=True)

    _running = True
    _thread = threading.Thread(
        target=cleanup_loop,
        daemon=True,
        name="SignalDVRCleanupService"
    )
    _thread.start()


def stop_cleanup_service():
    global _running
    _running = False
