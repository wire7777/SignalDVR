
from pathlib import Path

from app.stream_engine import index



def watch_from_beginning(session_dir):
    """
    Return the playlist URL and the first segment
    belonging to the current program.
    """

    start = current_program_start_segment(session_dir)

    if not start:
        return None

    return {
        "playlist": "live.m3u8",
        "segment": start["file"],
        "program": start.get("title", "")
    }

def rows(session_dir):
    return index.load_index(session_dir)


def segment_count(session_dir):
    return len(rows(session_dir))


def beginning_segment(session_dir):
    data = rows(session_dir)

    if not data:
        return None

    return data[0]


def live_segment(session_dir):
    data = rows(session_dir)

    if not data:
        return None

    return data[-1]


def current_program_segments(session_dir):
    data = rows(session_dir)

    if not data:
        return []

    latest = data[-1]
    program_id = latest.get("program_id")

    if not program_id:
        return []

    return [
        row for row in data
        if row.get("program_id") == program_id
    ]


def current_program_start_segment(session_dir):
    segments = current_program_segments(session_dir)

    if not segments:
        return None

    return segments[0]


def playback_status(session_dir):
    data = rows(session_dir)

    if not data:
        return {
            "ok": False,
            "segments": 0,
        }

    first = data[0]
    last = data[-1]

    return {
        "ok": True,
        "segments": len(data),
        "first_segment": first.get("file"),
        "live_segment": last.get("file"),
        "current_program": {
            "program_id": last.get("program_id"),
            "title": last.get("title"),
            "subtitle": last.get("subtitle"),
            "start": last.get("start"),
            "stop": last.get("stop"),
        },
        "current_program_start_segment": (
            current_program_start_segment(session_dir) or {}
        ).get("file"),
    }