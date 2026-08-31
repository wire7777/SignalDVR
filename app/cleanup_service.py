import shutil
import time
import threading
from pathlib import Path

from app import config
from app import database


# Runs more often than the old hourly interval so the buffer's auto-delete
# window (as low as 1 hour in Settings) is enforced reasonably promptly.
CHECK_INTERVAL = 15 * 60       # run every 15 minutes
MAX_AGE_SECONDS = 60 * 60      # delete stray temp/runtime files older than 1 hour

# Floor so a very short "Live Buffer Length" setting can't cause us to rip
# out a session directory that's only briefly between watches.
MIN_SESSION_RETENTION_SECONDS = 10 * 60

_running = False
_thread = None


def _is_old(path: Path, max_age_seconds: int = MAX_AGE_SECONDS) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        return age > max_age_seconds
    except FileNotFoundError:
        return False


def _safe_delete(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except Exception as e:
        print("Cleanup delete error:", path, e, flush=True)

    return False


def cleanup_livebuffer():
    removed = 0

    patterns = [
        "*.ts",
        "*.m3u8",
        "*.log",
        "*.tmp",
        "*.part",
    ]

    for pattern in patterns:
        for path in config.LIVEBUFFER.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def cleanup_runtime_files():
    removed = 0
    base = config.BASE

    patterns = [
        "*.pid",
        "*_current.txt",
        "*_position.txt",
        "*_channel.txt",
    ]

    for pattern in patterns:
        for path in base.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def cleanup_temp_thumbnails():
    removed = 0

    patterns = [
        "tmp*",
        "*.tmp",
        "*.part",
    ]

    for pattern in patterns:
        for path in config.THUMBNAILS.glob(pattern):
            if _is_old(path):
                if _safe_delete(path):
                    removed += 1

    return removed


def _retention_seconds():
    # Imported lazily to avoid any import-order issues at module load time.
    from app.stream_engine import manager as stream_engine
    return stream_engine.live_buffer_retention_seconds()


def cleanup_stale_live_sessions():
    """
    Auto-delete step for abandoned live-buffer sessions (channels you've
    tuned away from). While you're actively watching a channel,
    stream_engine.buffer.cleanup_segments() already trims that session
    down to the "Live Buffer Length" setting on every tune/poll. But once you
    switch channels, that session's ffmpeg process stops and nothing was
    trimming it anymore - its segment files would otherwise sit on disk
    forever. This removes the whole session folder once its newest segment
    is older than the configured retention window.

    The currently-active session is always skipped here.
    """
    sessions_dir = config.LIVEBUFFER / "sessions"

    if not sessions_dir.exists():
        return 0

    from app.stream_engine import manager as stream_engine

    active = stream_engine.get_active()
    active_session_id = (active or {}).get("session_id")

    max_age = max(_retention_seconds(), MIN_SESSION_RETENTION_SECONDS)
    removed = 0

    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue

        if active_session_id and session_dir.name == active_session_id:
            continue

        segments = list(session_dir.glob("segment_*.ts"))

        try:
            newest_mtime = max((s.stat().st_mtime for s in segments), default=session_dir.stat().st_mtime)
        except FileNotFoundError:
            continue

        if (time.time() - newest_mtime) > max_age:
            try:
                shutil.rmtree(session_dir, ignore_errors=True)
                removed += 1
            except Exception as e:
                print("cleanup_stale_live_sessions error:", session_dir, e, flush=True)

    return removed


def _dedicated_program_folder(file_path):
    """
    Return a resolved dedicated Background DVR program folder, or None.

    A deletable folder must have a structure equivalent to:

        programs/<channel>/<date>/<program-folder>

    Shared live paths containing a sessions directory are never returned.
    Symlinks are also rejected.
    """
    value = str(file_path or "").strip()

    if not value:
        return None

    try:
        original = Path(value).expanduser()

        if original.is_symlink():
            return None

        resolved = original.resolve(strict=False)
        lowered_parts = [part.lower() for part in resolved.parts]

        if "sessions" in lowered_parts:
            return None

        program_indexes = [
            index
            for index, part in enumerate(lowered_parts)
            if part == "programs"
        ]

        if not program_indexes:
            return None

        programs_index = program_indexes[-1]

        # Require channel, date, and a unique program directory below programs/.
        components_below_programs = len(resolved.parts) - programs_index - 1

        if components_below_programs < 3:
            return None

        if resolved.name.lower() in {"programs", "sessions"}:
            return None

        return resolved

    except Exception:
        return None


def cleanup_background_dvr(limit=500):
    """
    Permanently remove expired unsaved Background DVR programs.

    Dedicated folders below a programs/<channel>/<date>/<program> hierarchy
    are deleted before their database rows.

    Legacy rows that reference shared session folders are removed from the
    database only. Their shared folders are never touched.
    """
    stats = {
        "program_folders_deleted": 0,
        "program_rows_deleted": 0,
        "legacy_rows_deleted": 0,
        "errors": 0,
    }

    try:
        rows = database.list_expired_live_program_segments(limit=limit)
    except Exception as e:
        print("cleanup_background_dvr query error:", e, flush=True)
        stats["errors"] += 1
        return stats

    for row in rows:
        segment_id = row.get("id")
        title = row.get("title") or ""
        file_path = row.get("file_path") or ""
        program_folder = _dedicated_program_folder(file_path)
        is_legacy_path = program_folder is None

        if program_folder is not None and program_folder.exists():
            if not program_folder.is_dir():
                print(
                    "Background DVR cleanup refused non-directory path:",
                    program_folder,
                    flush=True,
                )
                stats["errors"] += 1
                continue

            try:
                shutil.rmtree(program_folder)
                stats["program_folders_deleted"] += 1
                print(
                    "Background DVR folder deleted:",
                    f"id={segment_id}",
                    f"title={title!r}",
                    f"path={program_folder}",
                    flush=True,
                )
            except Exception as e:
                print(
                    "Background DVR folder delete error:",
                    f"id={segment_id}",
                    program_folder,
                    e,
                    flush=True,
                )
                stats["errors"] += 1

                # Preserve the row when its dedicated media folder could not
                # be removed so cleanup can safely retry on the next cycle.
                continue

        try:
            deleted = database.delete_expired_live_program_segment(segment_id)

            if deleted:
                stats["program_rows_deleted"] += deleted

                if is_legacy_path:
                    stats["legacy_rows_deleted"] += deleted

        except Exception as e:
            print(
                "Background DVR row delete error:",
                f"id={segment_id}",
                e,
                flush=True,
            )
            stats["errors"] += 1

    return stats



def _folder_size(path: Path) -> int:
    total = 0
    if not path.exists() or not path.is_dir():
        return 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return total


def cleanup_background_dvr_quota():
    """Enforce the configured Background DVR storage limit safely.

    Only oldest unsaved, completed program folders are eligible. Saved shows,
    active programs, recordings, and shared live session folders are never
    removed by quota enforcement.
    """
    settings = database.get_background_dvr_settings()
    limit_gb = int(settings.get("storage_limit_gb") or 0)
    policy = settings.get("when_full") or "delete_oldest"

    stats = {"quota_rows_deleted": 0, "quota_folders_deleted": 0, "quota_bytes_freed": 0, "quota_over_limit": False}
    if limit_gb <= 0:
        return stats

    programs_root = config.LIVEBUFFER / "programs"
    used_bytes = _folder_size(programs_root)
    limit_bytes = limit_gb * 1024 * 1024 * 1024
    stats["quota_over_limit"] = used_bytes > limit_bytes

    if used_bytes <= limit_bytes or policy != "delete_oldest":
        return stats

    for row in database.list_background_dvr_storage_candidates():
        if used_bytes <= limit_bytes:
            break
        folder = _dedicated_program_folder(row.get("file_path"))
        if folder is None:
            continue
        size = _folder_size(folder)
        try:
            if folder.exists():
                shutil.rmtree(folder)
                stats["quota_folders_deleted"] += 1
            deleted = database.delete_live_program_segment(int(row["id"]))
            if deleted:
                stats["quota_rows_deleted"] += 1
                stats["quota_bytes_freed"] += size
                used_bytes = max(0, used_bytes - size)
                print("Background DVR quota cleanup:", f"id={row['id']}", f"freed={size}", flush=True)
        except Exception as error:
            print("Background DVR quota cleanup error:", row.get("id"), error, flush=True)

    return stats

def run_cleanup_once():
    livebuffer_removed = cleanup_livebuffer()
    runtime_removed = cleanup_runtime_files()
    thumb_removed = cleanup_temp_thumbnails()
    sessions_removed = cleanup_stale_live_sessions()
    background_dvr = cleanup_background_dvr()
    quota = cleanup_background_dvr_quota()

    folders_deleted = background_dvr["program_folders_deleted"]
    rows_deleted = background_dvr["program_rows_deleted"]
    legacy_rows_deleted = background_dvr["legacy_rows_deleted"]
    cleanup_errors = background_dvr["errors"]

    total = (
        livebuffer_removed
        + runtime_removed
        + thumb_removed
        + sessions_removed
        + folders_deleted
        + rows_deleted
        + quota["quota_folders_deleted"]
        + quota["quota_rows_deleted"]
    )

    print(
        "Cleanup complete:",
        f"livebuffer={livebuffer_removed}",
        f"runtime={runtime_removed}",
        f"thumbnails={thumb_removed}",
        f"stale_sessions={sessions_removed}",
        f"program_folders_deleted={folders_deleted}",
        f"program_rows_deleted={rows_deleted}",
        f"legacy_rows_deleted={legacy_rows_deleted}",
        f"quota_folders_deleted={quota['quota_folders_deleted']}",
        f"quota_rows_deleted={quota['quota_rows_deleted']}",
        f"quota_bytes_freed={quota['quota_bytes_freed']}",
        f"quota_over_limit={quota['quota_over_limit']}",
        f"errors={cleanup_errors}",
        f"total={total}",
        flush=True,
    )

    return {
        "livebuffer": livebuffer_removed,
        "runtime": runtime_removed,
        "thumbnails": thumb_removed,
        "stale_sessions": sessions_removed,
        "program_folders_deleted": folders_deleted,
        "program_rows_deleted": rows_deleted,
        "legacy_rows_deleted": legacy_rows_deleted,
        "quota_folders_deleted": quota["quota_folders_deleted"],
        "quota_rows_deleted": quota["quota_rows_deleted"],
        "quota_bytes_freed": quota["quota_bytes_freed"],
        "quota_over_limit": quota["quota_over_limit"],
        "errors": cleanup_errors,
        "total": total,
    }


def cleanup_loop():
    global _running

    print("SignalDVR cleanup service started", flush=True)

    while _running:
        try:
            run_cleanup_once()
        except Exception as e:
            print("Cleanup service error:", e, flush=True)

        time.sleep(CHECK_INTERVAL)


def start_cleanup_service():
    global _running, _thread

    if _running:
        print("SignalDVR cleanup service already running", flush=True)
        return

    print("Starting SignalDVR cleanup service...", flush=True)

    _running = True
    _thread = threading.Thread(
        target=cleanup_loop,
        daemon=True,
        name="SignalDVRCleanupService"
    )
    _thread.start()


def stop_cleanup_service():
    global _running
    _running = False
