import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


class ResumeManagerError(RuntimeError):
    pass


COMPLETION_PERCENT = 0.97
COMPLETION_REMAINING_SECONDS = 30.0


def _resolve_media_path(media_path: str | Path) -> Path:
    """
    Resolve and validate a media file or media directory.

    A regular recording stores its resume data beside the media file:
        recording.ts.resume.json

    A Background DVR / saved-program directory stores:
        resume.json
    """
    path = Path(media_path).expanduser().resolve()

    if not path.exists():
        raise ResumeManagerError(f"Media path not found: {path}")

    if not path.is_file() and not path.is_dir():
        raise ResumeManagerError(f"Unsupported media path: {path}")

    return path


def resume_path_for(media_path: str | Path) -> Path:
    path = _resolve_media_path(media_path)

    if path.is_dir():
        return path / "resume.json"

    return path.with_suffix(path.suffix + ".resume.json")


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_completed(position_seconds: float, duration_seconds: float) -> bool:
    if duration_seconds <= 0:
        return False

    percent_complete = position_seconds / duration_seconds
    remaining = duration_seconds - position_seconds

    return (
        percent_complete >= COMPLETION_PERCENT
        or remaining <= COMPLETION_REMAINING_SECONDS
    )


def default_resume(
    item_id: int,
    media_path: str | Path,
    duration_seconds: float = 0.0,
    item_type: str = "media",
) -> dict[str, Any]:
    path = _resolve_media_path(media_path)

    return {
        "version": 1,
        "item_id": int(item_id),
        "item_type": str(item_type or "media"),
        "media_path": str(path),
        "position_seconds": 0.0,
        "resume_position_seconds": 0.0,
        "duration_seconds": round(
            max(0.0, float(duration_seconds or 0.0)),
            3,
        ),
        "completed": False,
        "updated_at": None,
    }


def load_resume(
    item_id: int,
    media_path: str | Path,
    duration_seconds: float = 0.0,
    item_type: str = "media",
) -> dict[str, Any]:
    path = _resolve_media_path(media_path)
    resume_path = resume_path_for(path)

    if not resume_path.exists():
        return default_resume(
            item_id=item_id,
            media_path=path,
            duration_seconds=duration_seconds,
            item_type=item_type,
        )

    try:
        data = json.loads(
            resume_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeManagerError(
            f"Could not read resume file: {resume_path.name}"
        ) from exc

    saved_duration = _float_value(
        data.get("duration_seconds"),
        duration_seconds,
    )

    if duration_seconds > 0:
        saved_duration = float(duration_seconds)

    saved_duration = max(0.0, saved_duration)

    position = max(
        0.0,
        _float_value(data.get("position_seconds")),
    )

    if saved_duration > 0:
        position = min(position, saved_duration)

    completed = bool(data.get("completed"))
    resume_position = 0.0 if completed else position

    return {
        "version": 1,
        "item_id": int(item_id),
        "item_type": str(
            data.get("item_type")
            or item_type
            or "media"
        ),
        "media_path": str(path),
        "position_seconds": round(position, 3),
        "resume_position_seconds": round(
            resume_position,
            3,
        ),
        "duration_seconds": round(saved_duration, 3),
        "completed": completed,
        "updated_at": data.get("updated_at"),
    }


def save_resume(
    item_id: int,
    media_path: str | Path,
    position_seconds: float,
    duration_seconds: float,
    completed: bool | None = None,
    item_type: str = "media",
) -> dict[str, Any]:
    path = _resolve_media_path(media_path)
    resume_path = resume_path_for(path)

    duration = max(
        0.0,
        float(duration_seconds or 0.0),
    )
    position = max(
        0.0,
        float(position_seconds or 0.0),
    )

    if duration > 0:
        position = min(position, duration)

    if completed is None:
        completed_value = _is_completed(
            position,
            duration,
        )
    else:
        completed_value = bool(completed)

    data = {
        "version": 1,
        "item_id": int(item_id),
        "item_type": str(item_type or "media"),
        "media_path": str(path),
        "position_seconds": round(position, 3),
        "duration_seconds": round(duration, 3),
        "completed": completed_value,
        "updated_at": datetime.now().isoformat(
            timespec="seconds"
        ),
    }

    resume_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=resume_path.parent,
        prefix=resume_path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(data, temporary, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)

    temporary_path.replace(resume_path)

    return {
        **data,
        "resume_position_seconds": (
            0.0
            if completed_value
            else data["position_seconds"]
        ),
    }


def clear_resume(
    item_id: int,
    media_path: str | Path,
    item_type: str = "media",
) -> dict[str, Any]:
    path = _resolve_media_path(media_path)
    resume_path = resume_path_for(path)

    deleted = False

    if resume_path.exists():
        resume_path.unlink()
        deleted = True

    return {
        "version": 1,
        "item_id": int(item_id),
        "item_type": str(item_type or "media"),
        "media_path": str(path),
        "deleted": deleted,
        "position_seconds": 0.0,
        "resume_position_seconds": 0.0,
        "duration_seconds": 0.0,
        "completed": False,
        "updated_at": None,
    }