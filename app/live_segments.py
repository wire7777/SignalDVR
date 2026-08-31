from pathlib import Path
import re
import threading

from app import database
from app.stream_engine import index


_monitor_lock = threading.Lock()
_monitor_stop = threading.Event()
_monitor_thread = None


def _monitor_loop(active, interval_seconds):
    """
    Periodically sync the active watched-live program row.
    """
    while not _monitor_stop.wait(interval_seconds):
        try:
            row = sync_active_segment(active)
            try:
                from app import background_dvr
                background_dvr.sync_active_program(active, row=row)
            except Exception as e:
                print("background DVR sync error:", e, flush=True)
        except Exception as e:
            print("live segment monitor error:", e, flush=True)


def start_segment_monitor(active, interval_seconds=5):
    """Start one lightweight background guide-boundary monitor."""
    global _monitor_thread

    if not active:
        return

    with _monitor_lock:
        if _monitor_thread and _monitor_thread.is_alive():
            return

        _monitor_stop.clear()
        _monitor_thread = threading.Thread(
            target=_monitor_loop,
            args=(dict(active), int(interval_seconds or 5)),
            name="SignalDVRLiveSegmentMonitor",
            daemon=True,
        )
        _monitor_thread.start()


def stop_segment_monitor():
    """Stop the background guide-boundary monitor."""
    global _monitor_thread

    with _monitor_lock:
        _monitor_stop.set()
        _monitor_thread = None


def _segment_number(filename):
    match = re.search(r"(\d+)(?=\.ts$)", str(filename or ""))
    return int(match.group(1)) if match else None


def _segment_rows(session_dir):
    """
    Return a fresh, sorted segment index for the live session.

    We rebuild from files so DB/live segment metadata never depends on stale
    segments.json after a livebuffer reset.
    """
    session_dir = Path(session_dir)

    rows = index.rebuild_index_from_files(session_dir)

    if not rows:
        rows = index.load_index(session_dir)

    rows = rows or []

    def sort_key(row):
        number = _segment_number(row.get("file"))
        return number if number is not None else 0

    rows = [
        row for row in rows
        if row.get("file") and (session_dir / row.get("file")).exists()
    ]

    rows.sort(key=sort_key)
    return rows


def _latest_segment(session_dir):
    rows = _segment_rows(session_dir)
    if not rows:
        return "", 0, rows
    return rows[-1].get("file", ""), len(rows), rows


def _oldest_segment(rows):
    if not rows:
        return ""
    return rows[0].get("file", "")


def _range_segment_count(rows, first_segment, last_segment):
    """Count segments from first_segment through last_segment, inclusive."""
    if not rows or not first_segment or not last_segment:
        return 0

    files = [row.get("file", "") for row in rows]

    try:
        start_index = files.index(first_segment)
        end_index = files.index(last_segment)
        if end_index >= start_index:
            return (end_index - start_index) + 1
    except ValueError:
        pass

    first_num = _segment_number(first_segment)
    last_num = _segment_number(last_segment)

    if first_num is not None and last_num is not None and last_num >= first_num:
        available = []
        for row in rows:
            name = row.get("file")
            num = _segment_number(name)
            if name and num is not None and first_num <= num <= last_num:
                available.append(row)
        return len(available)

    return 0


def _repair_first_segment_if_needed(rows, first_segment, latest_segment):
    """
    If livebuffer was deleted/reset, DB can still point to an old first segment
    such as segment_016394 while the new session has segment_000000..000016.

    In that case, reset the program's first segment to the oldest currently
    available segment so segment_count starts growing again.
    """
    if not rows or not latest_segment:
        return first_segment or latest_segment

    files = [row.get("file", "") for row in rows]

    if first_segment in files:
        return first_segment

    oldest = _oldest_segment(rows)

    if not first_segment:
        return oldest or latest_segment

    first_num = _segment_number(first_segment)
    latest_num = _segment_number(latest_segment)

    # Old DB row after buffer reset: first number is greater than new latest.
    if first_num is not None and latest_num is not None and first_num > latest_num:
        return oldest or latest_segment

    # Missing file for any other reason: clamp to oldest available.
    return oldest or latest_segment


def sync_active_segment(active):
    """
    Keep SQLite in sync with the watched live buffer.

    This does not change playback. It creates/rotates metadata rows as the
    guide program changes while the user remains on a channel.
    """
    if not active:
        return None

    session_id = active.get("session_id") or ""
    channel = active.get("channel") or ""
    guide_name = active.get("guide_name") or channel
    session_dir = active.get("session_dir") or ""

    if not session_id or not channel or not session_dir:
        return None

    program = database.get_current_program(channel) or {
        "title": "Live TV",
        "start": "",
        "stop": "",
    }

    latest_segment, _buffer_count, rows = _latest_segment(session_dir)
    program_key = database.live_program_key(channel, program)
    active_segment = database.get_active_live_program_segment(session_id, channel)

    if active_segment and active_segment.get("program_key") == program_key:
        first_segment = active_segment.get("first_segment") or latest_segment
        first_segment = _repair_first_segment_if_needed(rows, first_segment, latest_segment)

        program_segment_count = _range_segment_count(rows, first_segment, latest_segment)

        database.update_live_program_segment_bounds(
            active_segment["id"],
            first_segment=first_segment,
            last_segment=latest_segment,
            segment_count=program_segment_count,
        )

        active_segment["first_segment"] = first_segment
        active_segment["last_segment"] = latest_segment or active_segment.get("last_segment", "")
        active_segment["segment_count"] = program_segment_count
        return active_segment

    if active_segment:
        first_segment = active_segment.get("first_segment") or latest_segment
        first_segment = _repair_first_segment_if_needed(rows, first_segment, latest_segment)
        program_segment_count = _range_segment_count(rows, first_segment, latest_segment)

        database.close_live_program_segment(
            active_segment["id"],
            last_segment=latest_segment,
            segment_count=program_segment_count,
            status="buffered",
        )

    first_segment = latest_segment

    return database.open_live_program_segment(
        session_id=session_id,
        channel=channel,
        guide_name=guide_name,
        program=program,
        first_segment=first_segment,
        last_segment=latest_segment,
        segment_count=1 if latest_segment else 0,
        file_path=str(session_dir),
    )


def close_active_segment(active):
    if not active:
        return None

    session_id = active.get("session_id") or ""
    channel = active.get("channel") or ""
    session_dir = active.get("session_dir") or ""

    if not session_id or not channel:
        return None

    latest_segment = ""
    rows = []

    if session_dir:
        latest_segment, _buffer_count, rows = _latest_segment(session_dir)

    active_segment = database.get_active_live_program_segment(session_id, channel)

    if active_segment:
        first_segment = active_segment.get("first_segment") or latest_segment
        first_segment = _repair_first_segment_if_needed(rows, first_segment, latest_segment)
        program_segment_count = _range_segment_count(rows, first_segment, latest_segment)

        database.close_live_program_segment(
            active_segment["id"],
            last_segment=latest_segment,
            segment_count=program_segment_count,
            status="buffered",
        )
        active_segment["last_segment"] = latest_segment
        active_segment["segment_count"] = program_segment_count
        active_segment["status"] = "buffered"
        try:
            from app import background_dvr
            background_dvr.finalize_active_program(active, row=active_segment)
        except Exception as e:
            print("background DVR finalize error:", e, flush=True)

    return active_segment
