import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database
from app import tvmaze_artwork


REFRESH_SECONDS = 6 * 60 * 60
STARTUP_DELAY_SECONDS = 15
FAILED_RETRY_DAYS = 7

FAILED_CACHE_FILE = (
    Path(tvmaze_artwork.PROGRAM_ARTWORK_DIR)
    / "failed_artwork.json"
)

_started = False
_start_lock = threading.Lock()


def _row_to_dict(row):
    if row is None:
        return {}

    if isinstance(row, dict):
        return row

    try:
        return dict(row)
    except Exception:
        return {}


def _load_failed_cache():
    if not FAILED_CACHE_FILE.exists():
        return {}

    try:
        data = json.loads(
            FAILED_CACHE_FILE.read_text(
                encoding="utf-8",
            )
        )

        return data if isinstance(data, dict) else {}

    except Exception as error:
        print(
            f"Artwork failed-cache read error: {error}",
            flush=True,
        )
        return {}


def _save_failed_cache(cache):
    try:
        FAILED_CACHE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = FAILED_CACHE_FILE.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                cache,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        temporary.replace(FAILED_CACHE_FILE)

    except Exception as error:
        print(
            f"Artwork failed-cache write error: {error}",
            flush=True,
        )


def _should_retry(program_id, failed_cache):
    failed_at = failed_cache.get(program_id)

    if not failed_at:
        return True

    try:
        timestamp = datetime.fromisoformat(failed_at)

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(
                tzinfo=timezone.utc,
            )

        retry_after = timestamp + timedelta(
            days=FAILED_RETRY_DAYS,
        )

        return datetime.now(timezone.utc) >= retry_after

    except Exception:
        return True


def _program_id(program):
    return str(
        program.get("programid")
        or program.get("programID")
        or ""
    ).strip()


def _prefetch_program(program, failed_cache):
    program_id = _program_id(program)

    if not program_id:
        return "skipped"

    if not _should_retry(program_id, failed_cache):
        return "failed_cached"

    try:
        filename = tvmaze_artwork.cache_program_artwork(
            program_id
        )

        if filename:
            failed_cache.pop(program_id, None)

            print(
                f"Artwork ready: "
                f"{program.get('title') or program_id} "
                f"-> {filename}",
                flush=True,
            )

            return "ready"

        failed_cache[program_id] = (
            datetime.now(timezone.utc).isoformat()
        )

        return "not_found"

    except Exception as error:
        failed_cache[program_id] = (
            datetime.now(timezone.utc).isoformat()
        )

        print(
            f"Artwork prefetch failed for "
            f"{program_id}: {error}",
            flush=True,
        )

        return "error"


def prefetch_now_next():
    failed_cache = _load_failed_cache()

    seen_program_ids = set()

    checked = 0
    ready = 0
    skipped_failures = 0
    not_found = 0

    for channel_row in database.list_channels():
        channel = _row_to_dict(channel_row)

        try:
            enabled = int(
                channel.get("enabled", 1) or 0
            )

            if enabled != 1:
                continue

        except Exception:
            pass

        channel_number = str(
            channel.get("guide_number") or ""
        ).strip()

        if not channel_number:
            continue

        try:
            rows = database.get_programs_for_channel(
                channel_number,
                limit=2,
            )

        except Exception as error:
            print(
                f"Artwork program lookup failed for "
                f"channel {channel_number}: {error}",
                flush=True,
            )
            continue

        for row in rows:
            program = _row_to_dict(row)
            program_id = _program_id(program)

            if not program_id:
                continue

            if program_id in seen_program_ids:
                continue

            seen_program_ids.add(program_id)
            checked += 1

            result = _prefetch_program(
                program,
                failed_cache,
            )

            if result == "ready":
                ready += 1
            elif result == "failed_cached":
                skipped_failures += 1
            elif result in ("not_found", "error"):
                not_found += 1

    _save_failed_cache(failed_cache)

    print(
        "Artwork prefetch complete: "
        f"checked={checked} "
        f"ready={ready} "
        f"failed_skipped={skipped_failures} "
        f"not_found={not_found}",
        flush=True,
    )


def _run():
    time.sleep(STARTUP_DELAY_SECONDS)

    while True:
        try:
            prefetch_now_next()

        except Exception as error:
            print(
                f"Artwork service error: {error}",
                flush=True,
            )

        time.sleep(REFRESH_SECONDS)


def start_artwork_service():
    global _started

    with _start_lock:
        if _started:
            return

        _started = True

        thread = threading.Thread(
            target=_run,
            name="SignalDVR-Artwork",
            daemon=True,
        )

        thread.start()

        print(
            "SignalDVR artwork service started "
            "(startup + every 6 hours)",
            flush=True,
        )