from pathlib import Path
from urllib.parse import quote

from app import config
from app import database
from app import live_segments
from app import program_catalog
from app import resume_manager
from app.stream_engine import manager as stream_engine


def _resume_state(
    item_id,
    media_path,
    item_type,
    duration_seconds=0.0,
):
    """
    Return a client-friendly resume state.

    Missing or unreadable resume data must never prevent the Library API from
    loading. In that case, return an empty resume state.
    """
    empty = {
        "resume_position_seconds": 0.0,
        "resume_duration_seconds": max(
            0.0,
            float(duration_seconds or 0.0),
        ),
        "resume_completed": False,
        "resume_updated_at": "",
        "resume_progress_percent": 0,
        "has_resume": False,
    }

    if not item_id or not media_path:
        return empty

    try:
        state = resume_manager.load_resume(
            item_id=int(item_id),
            media_path=media_path,
            duration_seconds=float(duration_seconds or 0.0),
            item_type=item_type,
        )
    except Exception:
        return empty

    position = max(
        0.0,
        float(
            state.get("resume_position_seconds")
            or 0.0
        ),
    )
    duration = max(
        0.0,
        float(
            state.get("duration_seconds")
            or duration_seconds
            or 0.0
        ),
    )
    completed = bool(state.get("completed"))

    progress_percent = 0

    if duration > 0:
        progress_percent = max(
            0,
            min(
                100,
                int(round(position / duration * 100)),
            ),
        )

    return {
        "resume_position_seconds": round(position, 3),
        "resume_duration_seconds": round(duration, 3),
        "resume_completed": completed,
        "resume_updated_at": state.get("updated_at") or "",
        "resume_progress_percent": progress_percent,
        "has_resume": bool(
            position > 0
            and not completed
        ),
    }


def _metadata_fields(row):
    """
    Normalize rich guide metadata into one consistent API shape.
    """
    row = dict(row)

    is_new = bool(row.get("is_new"))
    original_air_date = row.get("originalairdate") or ""

    return {
        "season": int(row.get("season") or 0),
        "episode_title": (
            row.get("episode_title")
            or row.get("subtitle")
            or ""
        ),
        "is_new": is_new,
        "is_repeat": (
            not is_new
            and bool(original_air_date)
        ),
        "originalairdate": original_air_date,
        "show_type": row.get("show_type") or "",
        "entity_type": row.get("entity_type") or "",
        "genres": row.get("genres") or "",
        "rating": row.get("rating") or "",
        "runtime": int(row.get("runtime") or 0),
        "year": int(row.get("year") or 0),
        "language": row.get("language") or "",
        "video_properties": row.get("video_properties") or "",
        "audio_properties": row.get("audio_properties") or "",
        "artwork": row.get("artwork") or "",
    }


def _program_actions(row, item_type, playable):
    """
    Describe which actions clients may perform on a catalog program.

    URLs are relative so clients can use the same payload regardless of
    the SignalDVR server hostname or port.
    """
    row = dict(row)

    program_id = row.get("id")
    channel = str(row.get("channel") or "")
    saved = bool(row.get("saved"))

    play_url = ""
    save_url = ""
    delete_url = ""
    expire_url = ""
    resume_url = ""

    can_play = bool(playable)
    can_save = bool(program_id and not saved)

    media_path = str(
        row.get("file_path")
        or ""
    ).replace("\\", "/").lower()

    is_dedicated_program_folder = (
        "/programs/" in media_path
        and "/sessions/" not in media_path
    )

    can_delete = bool(
        program_id
        and item_type in {"buffered", "saved"}
        and is_dedicated_program_folder
    )

    can_resume = bool(
        program_id
        and item_type in {"buffered", "saved"}
        and is_dedicated_program_folder
    )
    can_expire = bool(
        program_id
        and item_type == "buffered"
        and not saved
    )

    if can_play:
        if program_id:
            play_url = (
                f"/api/program_catalog/"
                f"{int(program_id)}/playlist"
            )
        elif item_type == "live" and channel:
            play_url = (
                "/play/live/"
                + quote(channel, safe="")
            )

    if can_save:
        save_url = (
            f"/api/program_catalog/"
            f"{int(program_id)}/save"
        )

    if can_delete:
        delete_url = (
            f"/api/program_catalog/"
            f"{int(program_id)}/delete"
        )

    if can_resume:
        resume_url = (
            f"/api/program_catalog/"
            f"{int(program_id)}/resume"
        )

    if can_expire:
        expire_url = (
            f"/api/program_catalog/"
            f"{int(program_id)}/expire"
        )

    return {
        "can_play": can_play,
        "can_save": can_save,
        "can_delete": can_delete,
        "can_resume": can_resume,
        "can_expire": can_expire,
        "play_url": play_url,
        "save_url": save_url,
        "delete_url": delete_url,
        "resume_url": resume_url,
        "expire_url": expire_url,
        "actions": {
            "play": play_url or None,
            "save": save_url or None,
            "delete": delete_url or None,
            "resume": resume_url or None,
            "expire": expire_url or None,
        },
    }


def _recording_actions(row, playable):
    """
    Describe which actions clients may perform on a completed recording.
    """
    row = dict(row)
    recording_id = row.get("id")

    play_url = ""
    delete_url = ""
    resume_url = ""

    can_play = bool(playable and recording_id)
    can_delete = bool(recording_id)
    can_resume = bool(recording_id)

    if can_play:
        play_url = (
            f"/api/recordings/{int(recording_id)}"
            f"/vod?profile=media3"
        )

    if can_delete:
        delete_url = (
            f"/api/recordings/"
            f"{int(recording_id)}/delete"
        )

    if can_resume:
        resume_url = (
            f"/api/recordings/"
            f"{int(recording_id)}/resume"
        )

    return {
        "can_play": can_play,
        "can_save": False,
        "can_delete": can_delete,
        "can_resume": can_resume,
        "can_expire": False,
        "play_url": play_url,
        "save_url": "",
        "delete_url": delete_url,
        "resume_url": resume_url,
        "expire_url": "",
        "actions": {
            "play": play_url or None,
            "save": None,
            "delete": delete_url or None,
            "resume": resume_url or None,
            "expire": None,
        },
    }


def _program_item(
    row,
    item_type="program",
    playable=True,
    play_error="",
):
    row = dict(row)

    item = {
        "id": row.get("id"),
        "type": item_type,
        "status": row.get("status"),
        "saved": bool(row.get("saved")),
        "auto_expire": bool(row.get("auto_expire")),
        "playable": bool(playable),
        "play_error": play_error or "",
        "channel": row.get("channel"),
        "channel_name": row.get("guide_name"),
        "title": row.get("title") or "Unknown Program",
        "subtitle": row.get("subtitle") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "episode": row.get("episode") or "",
        "start_time": row.get("start_time") or "",
        "stop_time": row.get("stop_time") or "",
        "first_segment": row.get("first_segment") or "",
        "last_segment": row.get("last_segment") or "",
        "segment_count": int(row.get("segment_count") or 0),
        "file_path": row.get("file_path") or "",
        "program_id": row.get("program_id") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "ended_at": row.get("ended_at") or "",
        "expires_at": row.get("expires_at") or "",
    }

    item.update(_metadata_fields(row))
    item.update(
        _program_actions(
            row=row,
            item_type=item_type,
            playable=playable,
        )
    )

    if item.get("can_resume"):
        item.update(
            _resume_state(
                item_id=item.get("id"),
                media_path=item.get("file_path"),
                item_type="program",
                duration_seconds=item.get("runtime") or 0,
            )
        )
    else:
        item.update(
            _resume_state(
                item_id=None,
                media_path=None,
                item_type="program",
                duration_seconds=item.get("runtime") or 0,
            )
        )

    return item


def _recording_item(row):
    row = dict(row)

    filename = row.get("filename") or ""
    path = config.RECORDINGS / filename if filename else None
    playable = bool(path and path.exists())

    item = {
        "id": row.get("id"),
        "type": "recording",
        "status": row.get("status") or "Recorded",
        "saved": True,
        "auto_expire": False,
        "playable": playable,
        "play_error": (
            ""
            if playable
            else "Recording file is missing."
        ),
        "channel": row.get("channel") or "",
        "channel_name": row.get("channel") or "",
        "title": row.get("title") or filename or "Recording",
        "subtitle": row.get("subtitle") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "episode": row.get("episode") or "",
        "start_time": row.get("start_time") or "",
        "stop_time": row.get("end_time") or "",
        "filename": filename,
        "size_bytes": int(row.get("size_bytes") or 0),
        "processing_status": (
            row.get("processing_status")
            or "pending"
        ),
        "processing_percent": int(
            row.get("processing_percent")
            or 0
        ),
        "processing_step": row.get("processing_step") or "",
        "processing_error": row.get("processing_error") or "",
        "vod_ready": bool(row.get("vod_ready")),
        "processed_at": row.get("processed_at") or "",
    }

    item.update(_metadata_fields(row))
    item.update(
        _recording_actions(
            row=row,
            playable=playable,
        )
    )

    item.update(
        _resume_state(
            item_id=item.get("id"),
            media_path=path,
            item_type="recording",
            duration_seconds=item.get("runtime") or 0,
        )
    )

    return item


def _segment_exists(program, segment_name):
    if not segment_name:
        return False

    folder = Path(program.get("file_path") or "")
    return (folder / segment_name).exists()


def _program_playable(row):
    """
    Dedicated Background DVR folders are playable when they contain a VOD
    playlist and at least two segment files.

    Legacy first_segment and last_segment values are intentionally ignored.
    """
    row = dict(row)

    status = str(
        row.get("status")
        or ""
    ).lower()

    if status == "active":
        return True, ""

    folder_value = str(
        row.get("file_path")
        or ""
    ).strip()

    if not folder_value:
        return False, "Program media path is missing."

    folder = Path(folder_value)

    if not folder.exists() or not folder.is_dir():
        return False, "Program media folder is missing."

    playlist_path = folder / "index.m3u8"

    if not playlist_path.exists():
        playlists = list(folder.glob("*.m3u8"))

        if not playlists:
            return False, "Program playlist is missing."

    segment_count = sum(
        1
        for path in folder.glob("segment_*.ts")
        if path.is_file()
    )

    if segment_count <= 1:
        return False, "Program does not have enough media."

    if status in {"buffered", "saved"}:
        return True, ""

    return False, "Program is not playable."


def sync_active():
    active = stream_engine.get_active()

    if not active:
        return None, None

    try:
        program = live_segments.sync_active_segment(active)
    except Exception as exc:
        print(
            "library active sync error:",
            exc,
            flush=True,
        )
        program = None

    return active, program


def get_live():
    """
    Show Live Now whenever the stream engine has an active live session.
    """
    active, program = sync_active()

    if not active:
        return []

    if program:
        item = _program_item(
            program,
            "live",
            playable=True,
        )
        item["status"] = "active"
        item["type"] = "live"
        return [item]

    ready_status = active.get("ready_status") or {}
    channel = active.get("channel") or ""

    item = {
        "id": None,
        "type": "live",
        "status": "active",
        "saved": False,
        "auto_expire": True,
        "playable": True,
        "play_error": "",
        "channel": channel,
        "channel_name": (
            active.get("guide_name")
            or channel
            or ""
        ),
        "title": "Live TV",
        "subtitle": "",
        "description": "",
        "category": "",
        "episode": "",
        "season": 0,
        "episode_title": "",
        "is_new": False,
        "is_repeat": False,
        "originalairdate": "",
        "show_type": "",
        "entity_type": "",
        "genres": "",
        "rating": "",
        "runtime": 0,
        "year": 0,
        "language": "",
        "video_properties": "",
        "audio_properties": "",
        "artwork": "",
        "start_time": "",
        "stop_time": "",
        "first_segment": "",
        "last_segment": (
            ready_status.get("latest_segment")
            or ""
        ),
        "segment_count": int(
            ready_status.get("segment_count")
            or 0
        ),
        "file_path": active.get("session_dir") or "",
        "program_id": "",
        "created_at": active.get("started_at") or "",
        "updated_at": "",
        "ended_at": "",
    }

    item.update(
        _program_actions(
            row=item,
            item_type="live",
            playable=True,
        )
    )

    return [item]


def get_buffered(limit=100, include_expired=False):
    rows = program_catalog.list_programs(
        status="buffered",
        saved=False,
        limit=limit,
    )

    items = []

    for row in rows:
        playable, reason = _program_playable(row)

        # Always keep buffered catalog entries visible. If media is missing
        # or incomplete, show the item as unplayable with the reason instead
        # of silently dropping it from the Library.
        items.append(
            _program_item(
                row,
                "buffered",
                playable=playable,
                play_error=reason,
            )
        )

    return items


def get_saved(limit=100, include_unplayable=False):
    rows = program_catalog.list_programs(
        saved=True,
        limit=limit,
    )

    items = []

    for row in rows:
        playable, reason = _program_playable(row)

        if playable or include_unplayable:
            items.append(
                _program_item(
                    row,
                    "saved",
                    playable=playable,
                    play_error=reason,
                )
            )

    return items


def get_recorded(limit=100):
    rows = database.list_recordings()

    return [
        _recording_item(row)
        for row in rows
    ][: int(limit or 100)]


def get_continue_watching(
    limit=30,
    buffered=None,
    saved=None,
    recorded=None,
):
    """
    Return unfinished playback ordered by the most recently saved position.

    These are references to existing library items, not duplicate media.
    """
    if buffered is None:
        buffered = get_buffered(limit=100)

    if saved is None:
        saved = get_saved(limit=100)

    if recorded is None:
        recorded = get_recorded(limit=100)

    items = [
        item
        for item in (
            list(saved)
            + list(recorded)
            + list(buffered)
        )
        if bool(item.get("can_resume"))
        and bool(item.get("has_resume"))
        and not bool(item.get("resume_completed"))
        and float(
            item.get("resume_position_seconds")
            or 0.0
        ) > 0
    ]

    items.sort(
        key=lambda item: str(
            item.get("resume_updated_at")
            or ""
        ),
        reverse=True,
    )

    return items[: max(1, int(limit or 30))]


def get_library(limit=100, include_expired=False):
    live = get_live()

    buffered = get_buffered(
        limit=limit,
        include_expired=include_expired,
    )

    saved = get_saved(limit=limit)
    recorded = get_recorded(limit=limit)
    continue_watching = get_continue_watching(
        limit=min(int(limit or 100), 30),
        buffered=buffered,
        saved=saved,
        recorded=recorded,
    )

    return {
        "continue_watching": continue_watching,
        "live": live,
        "buffered": buffered,
        "saved": saved,
        "recorded": recorded,
        "counts": {
            "continue_watching": len(continue_watching),
            "live": len(live),
            "buffered": len(buffered),
            "saved": len(saved),
            "recorded": len(recorded),
            "total": (
                len(live)
                + len(buffered)
                + len(saved)
                + len(recorded)
            ),
        },
    }


def search_library(query, limit=100):
    q = str(query or "").strip().lower()
    data = get_library(limit=limit)

    if not q:
        return data

    def match(item):
        haystack = " ".join([
            str(item.get("title", "")),
            str(item.get("subtitle", "")),
            str(item.get("episode_title", "")),
            str(item.get("description", "")),
            str(item.get("channel", "")),
            str(item.get("channel_name", "")),
            str(item.get("category", "")),
            str(item.get("genres", "")),
            str(item.get("show_type", "")),
            str(item.get("originalairdate", "")),
            str(item.get("year", "")),
        ]).lower()

        return q in haystack

    data["continue_watching"] = [
        item
        for item in data.get("continue_watching", [])
        if match(item)
    ]

    data["live"] = [
        item
        for item in data["live"]
        if match(item)
    ]

    data["buffered"] = [
        item
        for item in data["buffered"]
        if match(item)
    ]

    data["saved"] = [
        item
        for item in data["saved"]
        if match(item)
    ]

    data["recorded"] = [
        item
        for item in data["recorded"]
        if match(item)
    ]

    data["counts"] = {
        "continue_watching": len(
            data.get("continue_watching", [])
        ),
        "live": len(data["live"]),
        "buffered": len(data["buffered"]),
        "saved": len(data["saved"]),
        "recorded": len(data["recorded"]),
        "total": (
            len(data["live"])
            + len(data["buffered"])
            + len(data["saved"])
            + len(data["recorded"])
        ),
    }

    return data