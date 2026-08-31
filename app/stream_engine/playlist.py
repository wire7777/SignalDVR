from pathlib import Path
import os
import shutil

from app.stream_engine import index


def _load_or_rebuild(session_dir):
    session_dir = Path(session_dir)
    rows = index.load_index(session_dir)

    if not rows:
        rows = index.rebuild_index_from_files(session_dir)

    return [
        row for row in rows
        if row.get("file") and (session_dir / row["file"]).exists()
    ]


def build_playlist_from_rows(output_dir, rows, output_name="program_start.m3u8"):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = [
        row for row in rows
        if row.get("file") and (output_dir / row["file"]).exists()
    ]

    if not selected:
        raise RuntimeError("No playable segments found")

    output_path = output_dir / output_name

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:6",
        f"#EXT-X-MEDIA-SEQUENCE:{selected[0].get('sequence', 0)}",
    ]

    for row in selected:
        duration = row.get("duration") or "4.000"
        try:
            duration = f"{float(duration):.3f}"
        except Exception:
            duration = "4.000"

        lines.append(f"#EXTINF:{duration},")
        lines.append(row["file"])

    lines.append("#EXT-X-ENDLIST")
    output_path.write_text("\n".join(lines) + "\n")

    return output_path.name


def _find_index(rows, segment_name):
    if not segment_name:
        return None

    for i, row in enumerate(rows):
        if row.get("file") == segment_name:
            return i

    return None


def build_playlist_from_range(
    session_dir,
    first_segment,
    last_segment="",
    output_name="program_range.m3u8",
    clamp=False,
):
    """
    Build a playlist from first_segment THROUGH last_segment, inclusive.

    This is for guide/program playback. Unlike build_playlist_from_segment(),
    this does not play from first_segment to the end of the current buffer.
    It only plays the exact program range.
    """
    session_dir = Path(session_dir)
    rows = _load_or_rebuild(session_dir)

    if not rows:
        raise RuntimeError("No segment index found")

    first_index = _find_index(rows, first_segment)
    last_index = _find_index(rows, last_segment) if last_segment else None

    clamped_start = False
    clamped_end = False

    if first_index is None:
        if not clamp:
            raise RuntimeError("Start segment not found in index")
        first_index = 0
        clamped_start = True

    if last_index is None:
        if last_segment and not clamp:
            raise RuntimeError("End segment not found in index")
        last_index = len(rows) - 1
        clamped_end = bool(last_segment)

    if last_index < first_index:
        if not clamp:
            raise RuntimeError("End segment is before start segment")
        last_index = len(rows) - 1
        clamped_end = True

    selected = rows[first_index:last_index + 1]

    if not selected:
        raise RuntimeError("No playable segment range found")

    playlist_name = build_playlist_from_rows(
        session_dir,
        selected,
        output_name=output_name,
    )

    return {
        "playlist": playlist_name,
        "rows": selected,
        "first_segment": selected[0].get("file", ""),
        "last_segment": selected[-1].get("file", ""),
        "requested_first_segment": first_segment,
        "requested_last_segment": last_segment,
        "clamped": bool(clamped_start or clamped_end),
        "clamped_start": clamped_start,
        "clamped_end": clamped_end,
        "segment_count": len(selected),
    }


def build_playlist_from_program_position(
    session_dir,
    first_segment,
    last_segment,
    position_seconds=0,
    delta_seconds=0,
    output_name="program_seek.m3u8",
    clamp=False,
    segment_seconds=4,
):
    """
    Build a seek playlist for program playback.

    Android sends the current playback position and the requested FF/RW delta.
    SignalDVR maps that to a segment boundary and returns a fresh playlist
    starting at the correct target segment.
    """
    session_dir = Path(session_dir)
    rows = _load_or_rebuild(session_dir)

    if not rows:
        raise RuntimeError("No segment index found")

    first_index = _find_index(rows, first_segment)
    last_index = _find_index(rows, last_segment) if last_segment else None

    clamped_start = False
    clamped_end = False

    if first_index is None:
        if not clamp:
            raise RuntimeError("Start segment not found in index")
        first_index = 0
        clamped_start = True

    if last_index is None:
        if last_segment and not clamp:
            raise RuntimeError("End segment not found in index")
        last_index = len(rows) - 1
        clamped_end = bool(last_segment)

    if last_index < first_index:
        if not clamp:
            raise RuntimeError("End segment is before start segment")
        last_index = len(rows) - 1
        clamped_end = True

    program_rows = rows[first_index:last_index + 1]

    if not program_rows:
        raise RuntimeError("No playable segment range found")

    segment_seconds = max(1, int(segment_seconds or 4))
    current_index = int(max(0, float(position_seconds or 0)) / segment_seconds)
    delta_index = int(float(delta_seconds or 0) / segment_seconds)

    target_index = current_index + delta_index
    target_index = max(0, min(len(program_rows) - 1, target_index))

    selected = program_rows[target_index:]

    if not selected:
        raise RuntimeError("No playable seek segment found")

    playlist_name = build_playlist_from_rows(
        session_dir,
        selected,
        output_name=output_name,
    )

    target_seconds = target_index * segment_seconds

    return {
        "playlist": playlist_name,
        "rows": selected,
        "target_segment": selected[0].get("file", ""),
        "first_segment": program_rows[0].get("file", ""),
        "last_segment": program_rows[-1].get("file", ""),
        "requested_first_segment": first_segment,
        "requested_last_segment": last_segment,
        "target_seconds": target_seconds,
        "delta_seconds": int(delta_seconds or 0),
        "position_seconds": float(position_seconds or 0),
        "clamped": bool(clamped_start or clamped_end),
        "clamped_start": clamped_start,
        "clamped_end": clamped_end,
        "segment_count": len(selected),
        "program_segment_count": len(program_rows),
    }


def build_playlist_from_segment(session_dir, start_segment, output_name="program_start.m3u8", clamp=True):
    """
    Build a playlist from one segment through the end of the current index.

    This remains useful for live seeking/watch-from-beginning.
    Program playback should use build_playlist_from_range().
    """
    session_dir = Path(session_dir)
    rows = _load_or_rebuild(session_dir)

    if not rows:
        raise RuntimeError("No segment index found")

    start_index = _find_index(rows, start_segment)

    clamped = False
    requested_segment = start_segment

    if start_index is None:
        if not clamp:
            raise RuntimeError("Start segment not found in index")
        start_index = 0
        clamped = True

    selected = rows[start_index:]
    output_name = build_playlist_from_rows(session_dir, selected, output_name=output_name)

    return {
        "playlist": output_name,
        "segment": selected[0].get("file", ""),
        "requested_segment": requested_segment,
        "clamped": clamped,
        "segment_count": len(selected),
    }


def copy_segment_range(source_dir, dest_dir, first_segment, last_segment=""):
    """
    Preserve a segment range from a rolling live-buffer folder into permanent storage.
    If the requested first segment expired, clamp to the oldest available segment.
    """
    source_dir = Path(source_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_or_rebuild(source_dir)

    if not rows:
        raise RuntimeError("No segment index found")

    first_index = _find_index(rows, first_segment)
    last_index = _find_index(rows, last_segment) if last_segment else None

    clamped_start = False
    clamped_end = False

    if first_index is None:
        first_index = 0
        clamped_start = True

    if last_index is None:
        last_index = len(rows) - 1
        clamped_end = bool(last_segment)

    if last_index < first_index:
        last_index = len(rows) - 1
        clamped_end = True

    selected = rows[first_index:last_index + 1]

    if not selected:
        raise RuntimeError("No playable segment range found")

    copied = []

    for row in selected:
        name = row.get("file")
        if not name:
            continue

        src = source_dir / name
        dst = dest_dir / name

        if not src.exists():
            continue

        if not dst.exists():
            try:
                os.link(src, dst)
            except Exception:
                shutil.copy2(src, dst)

        copied.append(dict(row))

    if not copied:
        raise RuntimeError("No segment files could be preserved")

    return {
        "rows": copied,
        "first_segment": copied[0].get("file", ""),
        "last_segment": copied[-1].get("file", ""),
        "segment_count": len(copied),
        "clamped_start": clamped_start,
        "clamped_end": clamped_end,
        "requested_first_segment": first_segment,
        "requested_last_segment": last_segment,
    }
