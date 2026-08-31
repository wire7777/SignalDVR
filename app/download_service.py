from pathlib import Path
import re

from flask import Response, send_file, stream_with_context

from app import config
from app import database
from app import program_catalog


_CHUNK_SIZE = 1024 * 1024


def _safe_download_name(value, fallback):
    name = str(value or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or fallback


def _program_download_name(program):
    title = _safe_download_name(
        program.get("title"),
        f"program-{program.get('id', 'video')}",
    )

    start_time = str(program.get("start_time") or "")[:8]
    if len(start_time) == 8 and start_time.isdigit():
        date_label = (
            f"{start_time[0:4]}-"
            f"{start_time[4:6]}-"
            f"{start_time[6:8]}"
        )
        title = f"{title} - {date_label}"

    return f"{title}.ts"


def _recording_download_name(recording, source_path):
    title = _safe_download_name(
        recording.get("title"),
        source_path.stem,
    )

    suffix = source_path.suffix or ".ts"
    return f"{title}{suffix}"


def _iter_files(paths):
    for path in paths:
        with path.open("rb") as source:
            while True:
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk


def download_program(program_id):
    """
    Stream one completed Background DVR program as one MPEG-TS download.

    Existing segment files are concatenated in playback order. No transcoding,
    remuxing, temporary output file, or quality change is performed.
    """
    result = program_catalog.build_program_playlist(program_id)
    program = result["program"]
    folder = Path(result["session_dir"]).resolve()

    segments = sorted(
        path
        for path in folder.glob("segment_*.ts")
        if path.is_file()
    )

    if len(segments) <= 1:
        raise RuntimeError(
            "Program does not have enough media to download"
        )

    total_bytes = sum(path.stat().st_size for path in segments)
    download_name = _program_download_name(program)

    response = Response(
        stream_with_context(_iter_files(segments)),
        mimetype="video/mp2t",
        direct_passthrough=True,
    )

    response.headers["Content-Disposition"] = (
        f'attachment; filename="{download_name}"'
    )
    response.headers["Content-Length"] = str(total_bytes)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    return response


def download_recording(recording_id):
    """
    Download the original recording file exactly as stored by SignalDVR.
    """
    recording = None

    for row in database.list_recordings():
        if int(row["id"]) == int(recording_id):
            recording = dict(row)
            break

    if not recording:
        raise FileNotFoundError("Recording not found")

    filename = str(recording.get("filename") or "").strip()
    if not filename:
        raise FileNotFoundError("Recording has no filename")

    recordings_root = Path(config.RECORDINGS).resolve()
    source_path = (recordings_root / filename).resolve()

    try:
        source_path.relative_to(recordings_root)
    except ValueError as error:
        raise RuntimeError("Invalid recording path") from error

    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError("Recording file not found")

    return send_file(
        source_path,
        as_attachment=True,
        download_name=_recording_download_name(
            recording,
            source_path,
        ),
        conditional=True,
        max_age=0,
    )
