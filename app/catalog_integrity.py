import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from app import config
from app import database


PROGRAM_FOLDER_RE = re.compile(
    r"^(?P<id>\d+)_(?P<title>.+)_(?P<time>\d{6})$"
)
VALID_STATUSES = {"active", "buffered", "saved", "recorded"}


def _text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _bool_int(value):
    if isinstance(value, bool):
        return 1 if value else 0
    return 1 if _text(value).lower() in {"1", "true", "yes", "on", "new"} else 0


def _first(info, *keys, default=""):
    for key in keys:
        value = info.get(key)
        if value not in (None, ""):
            return value
    return default


def _program_roots():
    roots = []

    configured = Path(config.LIVEBUFFER).expanduser() / "programs"
    roots.append(configured)

    legacy = Path(config.BASE).expanduser() / "livebuffer" / "programs"
    if legacy != configured:
        roots.append(legacy)

    unique = []
    seen = set()

    for root in roots:
        key = str(root.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(root)

    return unique


def _load_info(folder):
    info_path = folder / "info.json"

    if not info_path.is_file():
        return {}

    try:
        value = json.loads(info_path.read_text(errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _segments(folder):
    return sorted(
        path
        for path in folder.glob("segment_*.ts")
        if path.is_file()
    )


def _playlist(folder):
    preferred = folder / "index.m3u8"
    if preferred.is_file():
        return preferred

    alternatives = sorted(
        path
        for path in folder.glob("*.m3u8")
        if path.is_file() and "_seek" not in path.name
    )
    return alternatives[0] if alternatives else None


CATALOG_SETTLE_SECONDS = 120


def _folder_is_settling(folder, settle_seconds=CATALOG_SETTLE_SECONDS):
    """
    Return True while a program folder is still changing.

    Background DVR may finish the database row slightly before every hard-link
    and playlist update reaches the dedicated program folder. Treat folders
    modified during the short settling window as in-progress so the health page
    does not repeatedly report harmless, temporary metadata mismatches.
    """
    folder = Path(folder)

    if not folder.exists() or not folder.is_dir():
        return False

    newest_mtime = 0.0

    try:
        newest_mtime = folder.stat().st_mtime
    except OSError:
        return False

    for pattern in ("*.ts", "*.m3u8", "info.json"):
        for path in folder.glob(pattern):
            try:
                newest_mtime = max(newest_mtime, path.stat().st_mtime)
            except OSError:
                continue

    return (time.time() - newest_mtime) < max(0, int(settle_seconds))


def _parse_folder(folder):
    match = PROGRAM_FOLDER_RE.match(folder.name)
    if not match:
        return None

    try:
        date_text = folder.parent.name
        datetime.strptime(date_text, "%Y-%m-%d")
        channel = folder.parent.parent.name
    except Exception:
        return None

    return {
        "id": int(match.group("id")),
        "folder_title": match.group("title").replace("_", " ").strip(),
        "folder_time": match.group("time"),
        "date": date_text,
        "channel": channel,
    }


def _normalize_xmltv_time(value, fallback=""):
    raw = _text(value)
    digits = re.sub(r"\D", "", raw)

    if len(digits) >= 14:
        return digits[:14]

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime(
            "%Y%m%d%H%M%S"
        )
    except Exception:
        return fallback


def _sql_time(value, fallback=None):
    raw = _text(value)
    if not raw:
        return fallback

    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 14:
        try:
            return datetime.strptime(
                digits[:14], "%Y%m%d%H%M%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return fallback


def _row_from_folder(folder):
    parsed = _parse_folder(folder)
    if not parsed:
        return None, "unrecognized program folder layout"

    segments = _segments(folder)
    playlist = _playlist(folder)

    if len(segments) < 2:
        return None, "fewer than two media segments"

    if playlist is None:
        return None, "playlist is missing"

    info = _load_info(folder)
    fallback_start = parsed["date"].replace("-", "") + parsed["folder_time"]

    start_time = _normalize_xmltv_time(
        _first(info, "start_time", "start", "program_start", "startTime"),
        fallback=fallback_start,
    )
    stop_time = _normalize_xmltv_time(
        _first(info, "stop_time", "end_time", "stop", "end", "program_stop")
    )

    runtime = _int(_first(info, "runtime", "duration", "duration_seconds"), 0)
    if not stop_time and start_time and runtime > 0:
        try:
            start_dt = datetime.strptime(start_time, "%Y%m%d%H%M%S")
            stop_time = (start_dt + timedelta(seconds=runtime)).strftime(
                "%Y%m%d%H%M%S"
            )
        except Exception:
            pass

    title = _text(
        _first(info, "title", "program_title"),
        parsed["folder_title"],
    ) or parsed["folder_title"]

    program_id = _text(_first(info, "program_id", "programid", "programId"))
    program_key = _text(_first(info, "program_key", "programKey"))

    if not program_key:
        program_key = ":".join([
            parsed["channel"],
            program_id or f"RECOVERED-{parsed['id']}",
            start_time,
            stop_time,
            title,
        ])

    saved = _bool_int(_first(info, "saved", default=0))
    status = _text(_first(info, "status")).lower()

    if saved:
        status = "saved"
    elif status not in VALID_STATUSES:
        status = "buffered"

    session_id = _text(
        _first(info, "session_id", "session"),
        f"recovered-{parsed['channel'].replace('.', '_')}",
    )

    started_at = _sql_time(
        _first(info, "started_at", "start_time", "start"),
        fallback=_sql_time(start_time),
    )
    ended_at = _sql_time(
        _first(info, "ended_at", "end_time", "stop_time", "stop")
    )

    return {
        "id": parsed["id"],
        "session_id": session_id,
        "channel": parsed["channel"],
        "guide_name": _text(_first(info, "guide_name", "channel_name", "station")),
        "program_key": program_key,
        "program_id": program_id,
        "title": title,
        "subtitle": _text(_first(info, "subtitle")),
        "description": _text(_first(info, "description", "desc")),
        "category": _text(_first(info, "category")),
        "episode": _text(_first(info, "episode")),
        "start_time": start_time,
        "stop_time": stop_time,
        "first_segment": segments[0].name,
        "last_segment": segments[-1].name,
        "segment_count": len(segments),
        "file_path": str(folder),
        "status": status,
        "saved": saved,
        "auto_expire": 0 if saved else 1,
        "started_at": started_at,
        "ended_at": ended_at,
        "created_at": _sql_time(_first(info, "created_at"), fallback=started_at),
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "season": _int(_first(info, "season"), 0),
        "episode_title": _text(_first(info, "episode_title", "episodeTitle")),
        "is_new": _bool_int(_first(info, "is_new", "new")),
        "originalairdate": _text(
            _first(info, "originalairdate", "original_air_date")
        ),
        "show_type": _text(_first(info, "show_type", "showType")),
        "entity_type": _text(_first(info, "entity_type", "entityType")),
        "genres": _text(_first(info, "genres")),
        "rating": _text(_first(info, "rating")),
        "runtime": runtime,
        "year": _int(_first(info, "year"), 0),
        "language": _text(_first(info, "language")),
        "video_properties": _text(
            _first(info, "video_properties", "videoProperties")
        ),
        "audio_properties": _text(
            _first(info, "audio_properties", "audioProperties")
        ),
        "artwork": _text(_first(info, "artwork")),
        "expires_at": _sql_time(_first(info, "expires_at")),
    }, ""


def _scan_program_folders():
    folders = []
    seen = set()

    for root in _program_roots():
        if not root.exists():
            continue

        candidates = set()
        candidates.update(path.parent for path in root.rglob("info.json"))
        candidates.update(path.parent for path in root.rglob("*.m3u8"))

        for folder in candidates:
            key = str(folder.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            folders.append(folder)

    return sorted(folders, key=lambda path: str(path))


def _candidate_rows():
    """
    Build one best playable media candidate per program ID.

    The same program ID may exist in both the configured storage root and the
    legacy local livebuffer root. Prefer the candidate with the most segments;
    on ties, prefer the configured root.
    """
    configured_root = str(
        (Path(config.LIVEBUFFER).expanduser() / "programs").resolve(strict=False)
    )

    best_by_id = {}
    skipped = []

    for folder in _scan_program_folders():
        row, reason = _row_from_folder(folder)

        if row is None:
            skipped.append({
                "path": str(folder),
                "reason": reason,
            })
            continue

        row_id = int(row["id"])
        current = best_by_id.get(row_id)

        if current is None:
            best_by_id[row_id] = row
            continue

        candidate_count = _int(row.get("segment_count"))
        current_count = _int(current.get("segment_count"))

        candidate_configured = str(
            Path(row["file_path"]).resolve(strict=False)
        ).startswith(configured_root)

        current_configured = str(
            Path(current["file_path"]).resolve(strict=False)
        ).startswith(configured_root)

        if (
            candidate_count > current_count
            or (
                candidate_count == current_count
                and candidate_configured
                and not current_configured
            )
        ):
            best_by_id[row_id] = row

    return best_by_id, skipped


def verify():
    folder_rows, skipped = _candidate_rows()

    with database.connect() as db:
        catalog_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM live_program_segments ORDER BY id"
            ).fetchall()
        ]

    catalog_by_id = {
        int(row["id"]): row
        for row in catalog_rows
    }

    missing_ids = sorted(
        set(folder_rows) - set(catalog_by_id)
    )

    stale_ids = []
    mismatched_ids = []

    for row_id, row in catalog_by_id.items():
        status = _text(row.get("status")).lower()

        # Active programs are changing every few seconds. Their segment count,
        # first/last segment, and even folder state are not stable enough for
        # an integrity failure during startup or a health request.
        if status == "active":
            continue

        current_folder = Path(
            _text(row.get("file_path"))
        )

        # A completed row can briefly continue receiving program-folder links
        # and playlist updates. Ignore it until the folder has been quiet long
        # enough to compare stable metadata.
        if _folder_is_settling(current_folder):
            continue

        current_disk_row = None

        if current_folder.exists():
            current_disk_row, _ = _row_from_folder(
                current_folder
            )

        candidate = (
            current_disk_row
            or folder_rows.get(row_id)
        )

        if candidate is None:
            stale_ids.append(row_id)
            continue

        if (
            _text(row.get("file_path"))
            != candidate["file_path"]
            or _int(row.get("segment_count"))
            != candidate["segment_count"]
            or _text(row.get("first_segment"))
            != candidate["first_segment"]
            or _text(row.get("last_segment"))
            != candidate["last_segment"]
        ):
            mismatched_ids.append(row_id)

    return {
        "ok": True,
        "healthy": (
            not missing_ids
            and not stale_ids
            and not mismatched_ids
        ),
        "program_roots": [
            str(root)
            for root in _program_roots()
        ],
        "media_folders": len(folder_rows),
        "catalog_entries": len(catalog_rows),
        "missing_entries": len(missing_ids),
        "missing_ids": missing_ids,
        "stale_entries": len(stale_ids),
        "stale_ids": stale_ids,
        "metadata_mismatches": len(mismatched_ids),
        "mismatched_ids": mismatched_ids,
        "skipped_folders": len(skipped),
        "skipped": skipped[:100],
    }


def repair_missing():
    candidates_by_id, skipped = _candidate_rows()
    candidates = list(candidates_by_id.values())

    inserted = 0
    updated = 0
    already_present = 0
    errors = []

    with database.connect() as db:
        columns = [
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(live_program_segments)"
            ).fetchall()
        ]

        existing_rows = {
            int(row["id"]): dict(row)
            for row in db.execute(
                "SELECT * FROM live_program_segments"
            ).fetchall()
        }

        quoted_columns = ", ".join(
            f'"{name}"'
            for name in columns
        )

        placeholders = ", ".join(
            "?"
            for _ in columns
        )

        insert_sql = (
            "INSERT OR IGNORE INTO live_program_segments "
            f"({quoted_columns}) VALUES ({placeholders})"
        )

        for candidate in candidates:
            try:
                row_id = int(candidate["id"])
                current = existing_rows.get(row_id)

                if current is None:
                    db.execute(
                        insert_sql,
                        [
                            candidate.get(name)
                            for name in columns
                        ],
                    )

                    changed = int(
                        db.execute(
                            "SELECT changes()"
                        ).fetchone()[0]
                    )

                    if changed:
                        inserted += 1
                        existing_rows[row_id] = candidate
                    else:
                        already_present += 1

                    continue

                # Never rewrite active program metadata while it is still
                # growing. live_segments owns active-row progress updates.
                if _text(current.get("status")).lower() == "active":
                    already_present += 1
                    continue

                # Do not repair a folder while Background DVR is still
                # finalizing links or rewriting its playlist.
                if _folder_is_settling(candidate["file_path"]):
                    already_present += 1
                    continue

                differences = (
                    _text(current.get("file_path"))
                    != candidate["file_path"]
                    or _int(current.get("segment_count"))
                    != candidate["segment_count"]
                    or _text(current.get("first_segment"))
                    != candidate["first_segment"]
                    or _text(current.get("last_segment"))
                    != candidate["last_segment"]
                )

                if differences:
                    db.execute(
                        """
                        UPDATE live_program_segments
                        SET file_path=?,
                            first_segment=?,
                            last_segment=?,
                            segment_count=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                          AND status != 'active'
                        """,
                        (
                            candidate["file_path"],
                            candidate["first_segment"],
                            candidate["last_segment"],
                            candidate["segment_count"],
                            row_id,
                        ),
                    )

                    updated += int(
                        db.execute(
                            "SELECT changes()"
                        ).fetchone()[0]
                    )
                else:
                    already_present += 1

            except Exception as error:
                errors.append({
                    "id": candidate.get("id"),
                    "path": candidate.get("file_path"),
                    "error": str(error),
                })

        db.commit()

    report = verify()

    report.update({
        "repair": True,
        "inserted": inserted,
        "updated": updated,
        "already_present": already_present,
        "repair_errors": errors,
        "repair_error_count": len(errors),
        "skipped_folders": len(skipped),
        "skipped": skipped[:100],
    })

    return report

def startup_check(auto_repair=True):
    report = verify()

    print(
        "[SignalDVR] Background DVR catalog check: "
        f"folders={report['media_folders']} "
        f"catalog={report['catalog_entries']} "
        f"missing={report['missing_entries']} "
        f"stale={report['stale_entries']} "
        f"mismatched={report['metadata_mismatches']}",
        flush=True,
    )

    if auto_repair and (
        report["missing_entries"] > 0
        or report["metadata_mismatches"] > 0
    ):
        repaired = repair_missing()
        print(
            "[SignalDVR] Background DVR catalog repair: "
            f"inserted={repaired['inserted']} "
            f"updated={repaired['updated']} "
            f"errors={repaired['repair_error_count']}",
            flush=True,
        )
        return repaired

    return report