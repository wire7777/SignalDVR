import datetime
import shutil
from pathlib import Path
from app.stream_engine import index
from app import config, database


SEGMENT_SECONDS = 4


def _safe_name(value):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value))


def save_buffer_minutes_as_recording(session_data, minutes=5, title=None):
    if not session_data:
        raise RuntimeError("No active stream session")

    session_dir = Path(session_data["session_dir"])
    channel = session_data["channel"]

    if not session_dir.exists():
        raise RuntimeError("Session directory not found")

    keep_segments = max(1, int((int(minutes) * 60) / SEGMENT_SECONDS))

    rows = index.latest_segments(session_dir, keep_segments)
    segments = [session_dir / row["file"] for row in rows]

    if not segments:
        raise RuntimeError("No buffer segments found")

    now = datetime.datetime.now()
    safe_channel = _safe_name(channel)
    safe_title = _safe_name(title or f"Live_Buffer_Last_{minutes}_Min")

    filename = f"{safe_channel}_{safe_title}_{now.strftime('%Y%m%d_%H%M%S')}.ts"
    output_path = config.RECORDINGS / filename

    config.RECORDINGS.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as out:
        for segment in segments:
            with open(segment, "rb") as inp:
                shutil.copyfileobj(inp, out)

    database.add_recording(
        filename=filename,
        channel=channel,
        title=title or f"Live Buffer Last {minutes} Min",
        start_time=now.strftime("%Y%m%d%H%M%S"),
        status="completed",
        size_bytes=output_path.stat().st_size,
    )

    return filename