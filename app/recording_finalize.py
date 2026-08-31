"""Background post-processing for completed recordings.

This module deliberately reuses the existing playback index and Media3 VOD
builder. It does not change playback routes, playlist format, seek behavior,
or Media3 playback logic.
"""

import datetime
import queue
import threading
from pathlib import Path

from app import config
from app import database
from app import playback_index
from app.stream_engine import vod


_jobs: "queue.Queue[str]" = queue.Queue()
_queued: set[str] = set()
_lock = threading.Lock()
_started = False


def _set_state(
    filename: str,
    status: str,
    percent: int,
    step: str,
    *,
    error: str = "",
    vod_ready: bool = False,
    processed_at: str = "",
) -> None:
    database.update_recording_processing(
        filename=filename,
        status=status,
        percent=percent,
        step=step,
        error=error,
        vod_ready=vod_ready,
        processed_at=processed_at,
    )


def _process(filename: str) -> None:
    source = config.RECORDINGS / filename
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Recording file is missing: {filename}")

    _set_state(filename, "processing", 10, "Building playback index")
    index_result = playback_index.build_index(filename, force=True)
    print(
        f"Playback index created: {filename} "
        f"duration={index_result.get('duration_seconds')} "
        f"seek_points={index_result.get('seek_point_count')}",
        flush=True,
    )

    # This is the exact same proven builder used by the existing Play endpoint.
    # force=False preserves and reuses a current cached VOD when one exists.
    _set_state(filename, "processing", 25, "Preparing Media3 playback")
    result = vod.build_vod(filename=filename, profile="media3", force=False)

    playlist = Path(result.get("playlist_path") or "")
    if not playlist.exists() or playlist.stat().st_size <= 0:
        raise RuntimeError("Media3 playlist was not created")

    _set_state(filename, "processing", 95, "Finalizing recording")
    completed = datetime.datetime.now().isoformat(timespec="seconds")
    _set_state(
        filename,
        "ready",
        100,
        "Ready",
        vod_ready=True,
        processed_at=completed,
    )

    print(
        f"Recording ready: {filename} playlist={playlist}",
        flush=True,
    )


def _worker() -> None:
    while True:
        filename = _jobs.get()
        try:
            _process(filename)
        except Exception as exc:
            message = str(exc)[-2000:]
            print(f"Recording post-process failed: {filename}: {message}", flush=True)
            try:
                _set_state(
                    filename,
                    "failed",
                    0,
                    "Playback preparation failed",
                    error=message,
                    vod_ready=False,
                )
            except Exception as state_exc:
                print(
                    f"Could not save processing failure for {filename}: {state_exc}",
                    flush=True,
                )
        finally:
            with _lock:
                _queued.discard(filename)
            _jobs.task_done()


def enqueue_recording(filename: str) -> bool:
    """Queue a completed recording once and return whether it was added."""
    if not filename:
        return False

    with _lock:
        if filename in _queued:
            return False
        _queued.add(filename)

    _set_state(filename, "pending", 0, "Queued for playback preparation")
    _jobs.put(filename)
    return True


def finalize_recording(filename: str) -> None:
    """Compatibility hook called by recorder.py after the TS file is closed."""
    enqueue_recording(filename)


def start_postprocess_service() -> None:
    """Start one low-impact worker and recover unfinished completed recordings."""
    global _started

    with _lock:
        if _started:
            return
        _started = True

    thread = threading.Thread(
        target=_worker,
        name="recording-postprocess",
        daemon=True,
    )
    thread.start()

    recovered = 0
    for recording in database.list_recordings_needing_processing():
        filename = recording.get("filename") or ""
        source = config.RECORDINGS / filename
        if filename and source.exists() and enqueue_recording(filename):
            recovered += 1

    print(
        f"Recording post-process service started; recovered={recovered}",
        flush=True,
    )
