from pathlib import Path

from app.stream_engine import index
from app.stream_engine import playlist


SEGMENT_SECONDS = 4


def build_seek_playlist(session_dir, seconds=-30, output_name="seek.m3u8"):
    session_dir = Path(session_dir)

    rows = index.load_index(session_dir)

    if not rows:
        rows = index.rebuild_index_from_files(session_dir)

    rows = [
        row for row in rows
        if row.get("file") and (session_dir / row["file"]).exists()
    ]

    if not rows:
        raise RuntimeError("No available segments")

    segment_offset = int(abs(seconds) / SEGMENT_SECONDS)

    if seconds >= 0:
        return {
            "playlist": "live.m3u8",
            "segment": rows[-1]["file"],
            "seconds": seconds,
            "clamped": False,
            "live": True,
       }

    target_index = max(0, len(rows) - segment_offset)           

    start_segment = rows[target_index]["file"]

    playlist_result = playlist.build_playlist_from_segment(
        session_dir,
        start_segment,
        output_name=output_name,
    )

    if isinstance(playlist_result, dict):
        playlist_name = playlist_result["playlist"]
        actual_segment = playlist_result.get("segment", start_segment)
        clamped = playlist_result.get("clamped", False)
    else:
        playlist_name = playlist_result
        actual_segment = start_segment
        clamped = False

    return {
        "playlist": playlist_name,
        "segment": actual_segment,
        "seconds": seconds,
        "clamped": clamped,
    }