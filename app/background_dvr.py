"""Guide-aware per-program HLS materializer for watched Live TV.

Phase 1 deliberately reuses the existing Playback Engine v2 live session. It
creates no second tuner lock and no second ffmpeg process. As segments appear in
the active session, they are hard-linked (or copied when linking is unavailable)
into a channel/date/program folder and a growing index.m3u8 is rewritten.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from app import config, database
from app.stream_engine.session import HLS_SEGMENT_SECONDS


PROGRAMS_DIR = config.LIVEBUFFER / "programs"
_lock = threading.Lock()
_service_lock = threading.Lock()
_service_stop = threading.Event()
_service_thread: threading.Thread | None = None
_service_state: dict[str, Any] = {
    "running": False,
    "ticks": 0,
    "last_tick_at": None,
    "last_error": None,
    "active_identity": None,
    "active_program_id": None,
}


def _safe(
    value: Any,
    fallback: str = "program",
    max_length: int = 80,
) -> str:
    text = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        str(value or "").strip(),
    )
    text = text.strip("._-") or fallback
    return text[:max_length]


def _program_dir(row: dict[str, Any]) -> Path:
    channel = _safe(
        row.get("channel"),
        "channel",
    )

    raw_start = str(
        row.get("start_time") or ""
    )[:14]

    try:
        start = dt.datetime.strptime(
            raw_start,
            "%Y%m%d%H%M%S",
        )
    except ValueError:
        start = dt.datetime.now()

    day = start.strftime("%Y-%m-%d")
    stamp = start.strftime("%H%M%S")

    title = _safe(
        row.get("title"),
        "Live_TV",
    )

    return (
        PROGRAMS_DIR
        / channel
        / day
        / f"{int(row['id']):06d}_{title}_{stamp}"
    )


def _segment_number(path: Path) -> int:
    match = re.search(
        r"(\d+)(?=\.ts$)",
        path.name,
    )

    return int(match.group(1)) if match else -1


def _ordered_program_segments(
    paths: list[Path],
    first_number: int,
    last_number: int | None,
) -> list[Path]:
    """Return program segments in playback order.

    Normal sequence:

        segment_001000.ts
        ...
        segment_001500.ts

    FFmpeg restart or rollover:

        segment_001696.ts
        ...
        segment_001763.ts
        segment_000001.ts
        ...
        segment_000093.ts

    When the last segment number is lower than the first segment number, the
    high-numbered portion must play before the restarted low-numbered portion.
    """

    valid = [
        path
        for path in paths
        if _segment_number(path) >= 0
    ]

    if first_number < 0:
        return sorted(
            valid,
            key=_segment_number,
        )

    # Active program with no known ending segment yet.
    if last_number is None or last_number < 0:
        return sorted(
            [
                path
                for path in valid
                if _segment_number(path) >= first_number
            ],
            key=_segment_number,
        )

    # Standard non-wrapped segment range.
    if last_number >= first_number:
        return sorted(
            [
                path
                for path in valid
                if first_number
                <= _segment_number(path)
                <= last_number
            ],
            key=_segment_number,
        )

    # FFmpeg restarted and reset numbering to segment_000001.ts.
    before_restart = sorted(
        [
            path
            for path in valid
            if _segment_number(path) >= first_number
        ],
        key=_segment_number,
    )

    after_restart = sorted(
        [
            path
            for path in valid
            if 0 <= _segment_number(path) <= last_number
        ],
        key=_segment_number,
    )

    return before_restart + after_restart


def _link_or_copy(
    source: Path,
    destination: Path,
) -> None:
    if destination.exists():
        return

    try:
        os.link(
            source,
            destination,
        )
    except OSError:
        shutil.copy2(
            source,
            destination,
        )


def _write_playlist(
    folder: Path,
    segment_names: list[str],
    finalized: bool,
) -> None:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{max(1, int(HLS_SEGMENT_SECONDS))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]

    for name in segment_names:
        lines.extend(
            (
                f"#EXTINF:{float(HLS_SEGMENT_SECONDS):.3f},",
                name,
            )
        )

    if finalized:
        lines.append("#EXT-X-ENDLIST")

    temp = folder / "index.m3u8.tmp"

    temp.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    temp.replace(
        folder / "index.m3u8"
    )


def _write_info(
    folder: Path,
    row: dict[str, Any],
    finalized: bool,
    segment_count: int,
) -> None:
    payload = {
        "id": int(row["id"]),
        "channel": row.get("channel") or "",
        "guide_name": row.get("guide_name") or "",
        "title": row.get("title") or "Live TV",
        "subtitle": row.get("subtitle") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "program_id": row.get("program_id") or "",
        "start": row.get("start_time") or "",
        "end": row.get("stop_time") or "",
        "first_segment": row.get("first_segment") or "",
        "last_segment": row.get("last_segment") or "",
        "segment_count": segment_count,
        "saved": bool(row.get("saved")),
        "status": "buffered" if finalized else "active",
        "finalized": finalized,
        "updated_at": dt.datetime.now().isoformat(
            timespec="seconds"
        ),
    }

    temp = folder / "info.json.tmp"

    temp.write_text(
        json.dumps(
            payload,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(
        folder / "info.json"
    )


def materialize_program(
    row: dict[str, Any],
    source_dir: str | Path,
    finalized: bool = False,
) -> dict[str, Any]:
    """Preserve one program's available segments and rebuild its playlist."""

    source = Path(source_dir)

    if not source.is_dir():
        raise RuntimeError(
            f"Live session directory does not exist: {source}"
        )

    first_name = str(
        row.get("first_segment") or ""
    )

    last_name = str(
        row.get("last_segment") or ""
    )

    if not first_name:
        return {
            "ok": True,
            "segment_count": 0,
            "folder": "",
        }

    first_number = _segment_number(
        Path(first_name)
    )

    last_number = (
        _segment_number(Path(last_name))
        if last_name
        else None
    )

    folder = _program_dir(row)

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_segments = list(
        source.glob("segment_*.ts")
    )

    selected_source = _ordered_program_segments(
        source_segments,
        first_number,
        last_number,
    )

    for segment in selected_source:
        _link_or_copy(
            segment,
            folder / segment.name,
        )

    # Build the playlist from the preserved destination files instead of only
    # the source session. The rolling source cleanup may already have removed
    # older segments, but their hard links or copies remain in this folder.
    preserved_segments = list(
        folder.glob("segment_*.ts")
    )

    ordered_preserved = _ordered_program_segments(
        preserved_segments,
        first_number,
        last_number,
    )

    names = [
        segment.name
        for segment in ordered_preserved
    ]

    _write_playlist(
        folder,
        names,
        finalized=finalized,
    )

    segment_count = len(names)
    last_segment = names[-1] if names else ""

    _write_info(
        folder,
        row,
        finalized=finalized,
        segment_count=segment_count,
    )

    # Keep the catalog pointing at the dedicated Background DVR folder.
    database.set_live_program_segment_file_path(
        int(row["id"]),
        str(folder),
    )

    # Keep SQLite synchronized with the actual program folder as it grows.
    database.update_live_program_segment_progress(
        int(row["id"]),
        last_segment=last_segment,
        segment_count=segment_count,
    )

    return {
        "ok": True,
        "segment_count": segment_count,
        "folder": str(folder),
    }


def sync_active_program(
    active: dict[str, Any],
    row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Materialize the active program and finalize prior program folders."""

    if not active:
        return None

    with _lock:
        current = (
            row
            or database.get_active_live_program_segment(
                active.get("session_id") or "",
                active.get("channel") or "",
            )
        )

        if not current:
            print(
                "background DVR: no active program row:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "session_dir": active.get("session_dir"),
                    "active_keys": sorted(active.keys()),
                },
                flush=True,
            )
            return None

        source_dir = (
            active.get("session_dir")
            or current.get("source_path")
            or ""
        )

        if not source_dir:
            print(
                "background DVR: no source directory:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "row_id": current.get("id"),
                },
                flush=True,
            )
            return None

        try:
            result = materialize_program(
                current,
                source_dir,
                finalized=False,
            )
        except Exception as exc:
            print(
                "background DVR: active materialize failed:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "row_id": current.get("id"),
                    "source_dir": str(source_dir),
                    "error": repr(exc),
                },
                flush=True,
            )
            return None

        previous_rows = database.list_live_program_segments(
            session_id=active.get("session_id"),
            channel=active.get("channel"),
            limit=20,
        )

        for old in previous_rows:
            if int(old["id"]) == int(current["id"]):
                continue

            if str(
                old.get("status") or ""
            ).lower() != "buffered":
                continue

            old_source = (
                old.get("source_path")
                or source_dir
            )

            try:
                materialize_program(
                    old,
                    old_source,
                    finalized=True,
                )
            except Exception as exc:
                print(
                    "background DVR: buffered materialize failed:",
                    {
                        "row_id": old.get("id"),
                        "channel": old.get("channel"),
                        "source_dir": str(old_source),
                        "error": repr(exc),
                    },
                    flush=True,
                )

        return result


def finalize_active_program(
    active: dict[str, Any],
    row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Finalize the currently active program playlist."""

    if not active:
        return None

    with _lock:
        current = (
            row
            or database.get_active_live_program_segment(
                active.get("session_id") or "",
                active.get("channel") or "",
            )
        )

        if not current:
            print(
                "background DVR: no program row to finalize:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "session_dir": active.get("session_dir"),
                },
                flush=True,
            )
            return None

        source_dir = (
            active.get("session_dir")
            or current.get("source_path")
            or ""
        )

        if not source_dir:
            print(
                "background DVR: no source directory for finalize:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "row_id": current.get("id"),
                },
                flush=True,
            )
            return None

        try:
            return materialize_program(
                current,
                source_dir,
                finalized=True,
            )
        except Exception as exc:
            print(
                "background DVR: finalize failed:",
                {
                    "session_id": active.get("session_id"),
                    "channel": active.get("channel"),
                    "row_id": current.get("id"),
                    "source_dir": str(source_dir),
                    "error": repr(exc),
                },
                flush=True,
            )
            return None

def _active_identity(active: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not active:
        return None

    return (
        str(active.get("session_id") or ""),
        str(active.get("channel") or ""),
        str(active.get("session_dir") or ""),
    )


def materializer_tick() -> dict[str, Any]:
    """Run one guide-aware Background DVR synchronization cycle."""
    from app import live_segments
    from app.stream_engine import manager as stream_engine

    active = stream_engine.get_active()
    identity = _active_identity(active)
    previous = _service_state.get("active_snapshot")
    previous_identity = _active_identity(previous)

    # A channel/session change must finalize the previous program before the
    # new session becomes authoritative. This never stops playback or ffmpeg.
    if previous and previous_identity != identity:
        try:
            live_segments.close_active_segment(previous)
        except Exception as exc:
            print(
                "background DVR: previous session finalize error:",
                repr(exc),
                flush=True,
            )

    row = None
    result = None

    if active:
        row = live_segments.sync_active_segment(active)
        result = sync_active_program(active, row=row)

    now = dt.datetime.now().isoformat(timespec="seconds")
    _service_state.update({
        "running": True,
        "ticks": int(_service_state.get("ticks") or 0) + 1,
        "last_tick_at": now,
        "last_error": None,
        "active_identity": list(identity) if identity else None,
        "active_snapshot": dict(active) if active else None,
        "active_program_id": int(row["id"]) if row and row.get("id") is not None else None,
        "last_result": result,
    })

    return {
        "active": active,
        "program": row,
        "materialized": result,
    }


def _materializer_loop(interval_seconds: float) -> None:
    _service_state["running"] = True

    while not _service_stop.is_set():
        try:
            materializer_tick()
        except Exception as exc:
            _service_state.update({
                "running": True,
                "last_tick_at": dt.datetime.now().isoformat(timespec="seconds"),
                "last_error": repr(exc),
            })
            print("background DVR materializer error:", repr(exc), flush=True)

        _service_stop.wait(max(1.0, float(interval_seconds)))

    _service_state["running"] = False


def start_materializer_service(interval_seconds: float = 3.0) -> None:
    """Start the single lightweight Phase 2 active-program materializer."""
    global _service_thread

    with _service_lock:
        if _service_thread and _service_thread.is_alive():
            return

        _service_stop.clear()
        _service_thread = threading.Thread(
            target=_materializer_loop,
            args=(float(interval_seconds),),
            name="SignalDVRBackgroundDVRMaterializer",
            daemon=True,
        )
        _service_thread.start()

    print(
        f"SignalDVR Background DVR materializer started; interval={float(interval_seconds):g}s",
        flush=True,
    )


def stop_materializer_service() -> None:
    global _service_thread

    with _service_lock:
        _service_stop.set()
        thread = _service_thread
        _service_thread = None

    if thread and thread.is_alive():
        thread.join(timeout=5)


def materializer_status() -> dict[str, Any]:
    thread = _service_thread
    state = dict(_service_state)
    state.pop("active_snapshot", None)
    state["running"] = bool(thread and thread.is_alive())
    state["thread_name"] = thread.name if thread else None
    return state


def status() -> dict[str, Any]:
    """Describe the isolated Phase 1 Background DVR subsystem."""
    from app.stream_engine import manager as stream_engine

    active = stream_engine.get_active()
    active_program = None

    if active:
        active_program = database.get_active_live_program_segment(
            active.get("session_id") or "",
            active.get("channel") or "",
        )

    # The materializer tracks the authoritative row ID. Use it as a safe
    # fallback during startup/recovery so status never reports null while a
    # program folder is actively being synchronized.
    if active_program is None:
        tracked_id = _service_state.get("active_program_id")
        if tracked_id is not None:
            active_program = database.get_live_program_segment(tracked_id)

    programs_root = PROGRAMS_DIR.resolve(strict=False)

    return {
        "ok": True,
        "enabled": True,
        "phase": 2,
        "mode": "active-guide-program-materialization",
        "materializer": materializer_status(),
        "programs_root": str(programs_root),
        "retention_seconds": int(
            stream_engine.live_buffer_retention_seconds() or 0
        ),
        "active_session": active,
        "active_program": active_program,
        "counts": database.get_background_dvr_stats(),
        "invariants": {
            "second_tuner_lock": False,
            "second_ffmpeg_process": False,
            "save_copies_media": False,
            "shared_session_deletion_allowed": False,
        },
    }

