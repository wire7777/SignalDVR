import shutil
from pathlib import Path

from app import database
from app import live_segments
from app.stream_engine import manager as stream_engine


PROGRAM_STATUSES = {
    "active",
    "buffered",
    "saved",
    "recorded",
}


def normalize_status(status):
    status = str(status or "").strip().lower()
    return status if status in PROGRAM_STATUSES else ""


def list_programs(
    status="",
    saved=None,
    channel="",
    limit=100,
):
    return database.list_program_catalog(
        status=normalize_status(status),
        saved=saved,
        channel=channel,
        limit=limit,
    )


def get_program(program_id):
    return database.get_live_program_segment(program_id)


def _resolve_program_folder(program):
    """
    Return the program's dedicated Background DVR folder.

    Deletion and playback are allowed only for paths that use the dedicated
    programs/<channel>/<date>/<program-folder> layout.

    Shared live session folders are never accepted.
    """
    file_path = str(
        (program or {}).get("file_path")
        or ""
    ).strip()

    if not file_path:
        raise RuntimeError("Program has no media path")

    raw_path = Path(file_path).expanduser()

    if raw_path.is_symlink():
        raise RuntimeError(
            "Symbolic-link program folders are not supported"
        )

    folder = raw_path.resolve(strict=False)
    lower_parts = [
        part.lower()
        for part in folder.parts
    ]

    if "sessions" in lower_parts:
        raise RuntimeError(
            "Refusing shared live-session path"
        )

    program_indexes = [
        index
        for index, part in enumerate(lower_parts)
        if part == "programs"
    ]

    if not program_indexes:
        raise RuntimeError(
            "Program path is outside the Background DVR programs root"
        )

    programs_index = program_indexes[-1]

    # Require:
    # programs / channel / date / unique-program-folder
    components_below_programs = (
        len(folder.parts)
        - programs_index
        - 1
    )

    if components_below_programs < 3:
        raise RuntimeError(
            "Program path is not a dedicated program folder"
        )

    return folder


def _find_program_playlist(folder):
    """
    Locate the dedicated Background DVR playlist.

    Background DVR writes index.m3u8 directly into each program folder.
    """
    preferred = folder / "index.m3u8"

    if preferred.exists() and preferred.is_file():
        return preferred

    alternatives = sorted(
        path
        for path in folder.glob("*.m3u8")
        if path.is_file()
        and "_seek" not in path.name
    )

    if alternatives:
        return alternatives[0]

    raise RuntimeError(
        "Program playlist is missing"
    )


def _program_segments(folder):
    return sorted(
        path
        for path in folder.glob("segment_*.ts")
        if path.is_file()
    )


def save_program(program_id):
    """
    Save a Background DVR program permanently.

    The program folder is already the recording, so saving only changes its
    lifecycle metadata. No media is copied or moved.
    """
    program = get_program(program_id)

    if not program:
        return None

    status = str(
        program.get("status")
        or ""
    ).lower()

    if status != "active":
        folder = _resolve_program_folder(program)

        if not folder.exists():
            raise RuntimeError(
                "Program media folder is missing"
            )

        _find_program_playlist(folder)

    return database.mark_live_program_segment_saved(
        program_id
    )


def expire_program(program_id):
    """
    Return a saved program to automatic expiration.
    """
    program = get_program(program_id)

    if not program:
        return None

    return database.mark_live_program_segment_expirable(
        program_id
    )


def delete_program(program_id):
    """
    Permanently delete a Background DVR program.

    The dedicated program folder is removed first. The database row is removed
    only after folder deletion succeeds.

    Shared session paths are always refused.
    """
    program = get_program(program_id)

    if not program:
        return None

    status = str(
        program.get("status")
        or ""
    ).lower()

    if status == "active":
        raise RuntimeError(
            "An active program cannot be deleted"
        )

    folder = _resolve_program_folder(program)

    if folder.exists():
        if not folder.is_dir():
            raise RuntimeError(
                "Program media path is not a directory"
            )

        shutil.rmtree(folder)

    deleted = database.delete_live_program_segment(
        program_id
    )

    if not deleted:
        raise RuntimeError(
            "Program database row could not be deleted"
        )

    return program


def build_program_playlist(
    program_id,
    output_name=None,
):
    """
    Return the playlist already written inside the dedicated Background DVR
    folder.

    first_segment and last_segment are intentionally not used. Those fields
    describe the original live-session range and may not match the dedicated
    folder's renumbered segment names.
    """
    program = get_program(program_id)

    if not program:
        raise RuntimeError("Program not found")

    status = str(
        program.get("status")
        or ""
    ).lower()

    if status == "active":
        active = stream_engine.get_active()

        if (
            active
            and active.get("session_id")
            == program.get("session_id")
        ):
            live_segments.sync_active_segment(active)
            program = (
                get_program(program_id)
                or program
            )

    folder = _resolve_program_folder(program)

    if not folder.exists():
        raise RuntimeError(
            "Program media folder is missing"
        )

    playlist_path = _find_program_playlist(folder)
    segments = _program_segments(folder)

    if len(segments) <= 1:
        raise RuntimeError(
            "Program does not have enough media to play"
        )

    return {
        "program": program,
        "playlist": playlist_path.name,
        "session_dir": str(folder),
        "clamped": False,
        "requested_first_segment": (
            program.get("first_segment")
            or ""
        ),
        "requested_last_segment": (
            program.get("last_segment")
            or ""
        ),
        "actual_first_segment": segments[0].name,
        "actual_last_segment": segments[-1].name,
        "segment_count": len(segments),
    }


def seek_program_playlist(
    program_id,
    position_seconds=0,
    delta_seconds=0,
    output_name=None,
):
    """
    Return the program's normal VOD playlist.

    Media3 performs seeking within the complete VOD playlist. The backend no
    longer rebuilds playlists from stale live-session segment ranges.
    """
    result = build_program_playlist(program_id)

    return {
        **result,
        "target_segment": "",
        "target_seconds": max(
            0,
            float(position_seconds or 0)
            + float(delta_seconds or 0),
        ),
        "position_seconds": float(
            position_seconds
            or 0
        ),
        "delta_seconds": float(
            delta_seconds
            or 0
        ),
        "program_segment_count": result[
            "segment_count"
        ],
    }
