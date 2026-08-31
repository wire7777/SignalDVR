import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app import config


INDEX_VERSION = 1
DEFAULT_INTERVAL_SECONDS = 2.0


class PlaybackIndexError(RuntimeError):
    """Raised when a recording playback index cannot be generated."""


def _recording_path(filename: str) -> Path:
    """
    Resolve a recording while preventing access outside the recording folder.
    """
    base = config.RECORDINGS.resolve()
    path = (base / filename).resolve()

    if path != base and base not in path.parents:
        raise PlaybackIndexError("Invalid recording path")

    if not path.exists() or not path.is_file():
        raise PlaybackIndexError(f"Recording not found: {filename}")

    return path


def index_path_for(recording_path: Path) -> Path:
    """
    Example:
        movie.ts -> movie.playback.json
    """
    return recording_path.with_suffix(
        recording_path.suffix + ".playback.json"
    )


def _run_ffprobe(recording_path: Path) -> dict[str, Any]:
    """
    Read container duration and video keyframe timestamps.

    ffprobe returns keyframes only, which gives us valid random-access
    positions instead of inventing seek points that may land between frames.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration,start_time:"
        "packet=pts_time,dts_time,pos,flags",
        "-show_packets",
        "-of",
        "json",
        str(recording_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PlaybackIndexError(
            "ffprobe timed out while indexing recording"
        ) from exc

    if result.returncode != 0:
        error = result.stderr.strip() or "ffprobe failed"
        raise PlaybackIndexError(error)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlaybackIndexError(
            "ffprobe returned invalid JSON"
        ) from exc


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_seek_points(
    probe: dict[str, Any],
    interval_seconds: float,
) -> tuple[float, float, list[dict[str, Any]]]:
    format_info = probe.get("format") or {}

    duration = max(
        0.0,
        _float_value(format_info.get("duration")),
    )

    source_start_time = _float_value(
        format_info.get("start_time"),
        0.0,
    )

    keyframes: list[dict[str, Any]] = []

    for packet in probe.get("packets") or []:
        flags = str(packet.get("flags") or "")

        if "K" not in flags:
            continue

        raw_timestamp = packet.get("pts_time")

        if raw_timestamp is None:
            raw_timestamp = packet.get("dts_time")

        timestamp = _float_value(raw_timestamp, -1.0)

        if timestamp < 0:
            continue

        normalized_seconds = max(
            0.0,
            timestamp - source_start_time,
        )

        keyframes.append({
            "time": round(normalized_seconds, 3),
            "source_time": round(timestamp, 3),
            "byte_offset": _int_value(packet.get("pos")),
        })

    keyframes.sort(key=lambda row: row["time"])

    # Remove duplicate timestamps.
    unique_keyframes: list[dict[str, Any]] = []
    seen_times: set[float] = set()

    for frame in keyframes:
        timestamp = frame["time"]

        if timestamp in seen_times:
            continue

        seen_times.add(timestamp)
        unique_keyframes.append(frame)

    if not unique_keyframes:
        raise PlaybackIndexError(
            "No MPEG video keyframes were found"
        )

    seek_points: list[dict[str, Any]] = []
    target = 0.0
    keyframe_index = 0
    previous_keyframe = unique_keyframes[0]

    while target <= duration:
        while (
            keyframe_index + 1 < len(unique_keyframes)
            and unique_keyframes[keyframe_index + 1]["time"] <= target
        ):
            keyframe_index += 1
            previous_keyframe = unique_keyframes[keyframe_index]

        seek_points.append({
            "target_seconds": round(target, 3),
            "keyframe_seconds": previous_keyframe["time"],
            "source_time": previous_keyframe["source_time"],
            "byte_offset": previous_keyframe["byte_offset"],
        })

        target += interval_seconds

    # Always include a final seek point near the end.
    if duration > 0:
        final_keyframe = unique_keyframes[-1]

        if (
            not seek_points
            or seek_points[-1]["target_seconds"] < duration
        ):
            seek_points.append({
                "target_seconds": round(duration, 3),
                "keyframe_seconds": final_keyframe["time"],
                "source_time": final_keyframe["source_time"],
                "byte_offset": final_keyframe["byte_offset"],
            })

    return duration, source_start_time, seek_points


def build_index(
    filename: str,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    force: bool = False,
) -> dict[str, Any]:
    """
    Build or reuse a playback index for one recording.
    """
    if interval_seconds <= 0:
        raise PlaybackIndexError(
            "Index interval must be greater than zero"
        )

    recording_path = _recording_path(filename)
    output_path = index_path_for(recording_path)

    if output_path.exists() and not force:
        existing = load_index(filename)

        source = existing.get("source") or {}

        if (
            source.get("size_bytes") == recording_path.stat().st_size
            and source.get("modified_ns")
            == recording_path.stat().st_mtime_ns
        ):
            return existing

    probe = _run_ffprobe(recording_path)

    duration, source_start_time, seek_points = _build_seek_points(
        probe,
        interval_seconds,
    )

    stat = recording_path.stat()

    data = {
        "version": INDEX_VERSION,
        "created_at": datetime.now().isoformat(),
        "filename": recording_path.name,
        "duration_seconds": round(duration, 3),
        "source_start_time": round(source_start_time, 3),
        "interval_seconds": interval_seconds,
        "seek_point_count": len(seek_points),
        "source": {
            "path": str(recording_path),
            "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        },
        "seek_points": seek_points,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically so a server interruption does not leave half a file.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=output_path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(data, temporary, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)

    temporary_path.replace(output_path)

    return data


def load_index(filename: str) -> dict[str, Any]:
    recording_path = _recording_path(filename)
    path = index_path_for(recording_path)

    if not path.exists():
        raise PlaybackIndexError(
            f"Playback index does not exist: {path.name}"
        )

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlaybackIndexError(
            f"Could not read playback index: {path.name}"
        ) from exc


def get_or_build_index(
    filename: str,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> dict[str, Any]:
    return build_index(
        filename=filename,
        interval_seconds=interval_seconds,
        force=False,
    )


def find_seek_point(
    filename: str,
    target_seconds: float,
) -> dict[str, Any]:
    """
    Return the nearest safe keyframe at or before the requested time.
    """
    index_data = get_or_build_index(filename)
    seek_points = index_data.get("seek_points") or []

    if not seek_points:
        raise PlaybackIndexError(
            "Playback index contains no seek points"
        )

    duration = _float_value(
        index_data.get("duration_seconds"),
        0.0,
    )

    target = max(0.0, float(target_seconds))

    if duration > 0:
        target = min(target, duration)

    selected = seek_points[0]

    for point in seek_points:
        if _float_value(point.get("target_seconds")) > target:
            break

        selected = point

    return {
        **selected,
        "requested_seconds": round(target, 3),
        "duration_seconds": duration,
    }


def delete_index(filename: str) -> bool:
    recording_path = _recording_path(filename)
    path = index_path_for(recording_path)

    if not path.exists():
        return False

    path.unlink()
    return True