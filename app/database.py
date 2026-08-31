import sqlite3
import datetime
import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("SIGNALDVR_DATABASE", str(BASE_DIR / "database" / "signaldvr.db"))).expanduser()


class _AutoClosingConnection(sqlite3.Connection):
    """A sqlite3.Connection that actually closes itself on 'with' exit.

    Plain sqlite3.Connection's context manager only commits/rolls back the
    transaction - it does NOT close the connection or release its file
    handle. Every call site in this file uses `with connect() as db:`,
    which means every single one of them was leaking an open file handle
    that was never released until Python's garbage collector eventually
    got around to it (not guaranteed to be prompt). Under enough request
    volume this exhausts the process's open-file limit, which then causes
    every kind of database call to start failing with
    "unable to open database file".

    Using this as the connection factory fixes every existing
    `with connect() as db:` call site at once, with no other code changes
    needed.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def connect():
    conn = sqlite3.connect(DB, timeout=30, factory=_AutoClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def save_sd_lineups(lineups):
    import json

    with connect() as db:
        db.execute("DELETE FROM sd_lineups")

        for lineup in lineups:
            lineup_id = lineup.get("lineup", "")
            name = lineup.get("name", lineup_id)
            location = lineup.get("location", "")
            transport = lineup.get("transport", "")

            db.execute("""
                INSERT OR REPLACE INTO sd_lineups
                (lineup_id, name, location, transport, raw_json)
                VALUES (?, ?, ?, ?, ?)
            """, (
                lineup_id,
                name,
                location,
                transport,
                json.dumps(lineup),
            ))

        db.commit()


def list_sd_lineups():
    try:
        with connect() as db:
            rows = db.execute("""
                SELECT *
                FROM sd_lineups
                ORDER BY name
            """).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.OperationalError as error:
        if "no such table" in str(error).lower():
            return []
        raise

def get_setting(key, default=""):
    with connect() as db:
        row = db.execute("""
            SELECT value
            FROM settings
            WHERE key=?
        """, (key,)).fetchone()

        return row["value"] if row else default


def set_setting(key, value):
    with connect() as db:
        db.execute("""
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))
        db.commit()


def get_all_settings():
    with connect() as db:
        rows = db.execute("""
            SELECT key, value
            FROM settings
            ORDER BY key
        """).fetchall()

        return {r["key"]: r["value"] for r in rows}


def _ensure_column(db, table, column, definition):
    cols = db.execute(f"PRAGMA table_info({table})").fetchall()
    names = [c["name"] for c in cols]
    if column not in names:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _parse_xmltv_time(value):
    return datetime.datetime.strptime(str(value)[:14], "%Y%m%d%H%M%S")


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)

    with connect() as db:
        db.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # Persist database metadata instead of relying on filesystem birth time,
        # which is commonly unavailable on Linux.
        db.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('database_created_at', CURRENT_TIMESTAMP)
        """)

        db.execute("""
            INSERT INTO settings(key, value)
            VALUES ('schema_version', '1')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """)

        db.execute("PRAGMA user_version = 1")

        db.execute("""
        CREATE TABLE IF NOT EXISTS sd_lineups(
            lineup_id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            location TEXT DEFAULT '',
            transport TEXT DEFAULT '',
            raw_json TEXT DEFAULT ''
        )
        """)

        db.execute("""
        CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guide_number TEXT UNIQUE,
            guide_name TEXT,
            url TEXT,
            favorite INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        )
        """)

        # SignalDVR no longer reads or writes this value. Keep the column to
        # avoid a destructive SQLite table migration on existing installs.

        db.execute("""
        CREATE TABLE IF NOT EXISTS recordings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            channel TEXT,
            title TEXT,
            start_time TEXT,
            end_time TEXT,
            size_bytes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Recorded'
        )
        """)

        # Recording metadata and post-processing state. These upgrades are
        # additive and do not alter the existing playback/VOD schema.
        _ensure_column(db, "recordings", "subtitle", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "description", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "category", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "season", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "episode", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "programid", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "seriesid", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "recording_rule", "INTEGER DEFAULT 0")
        _ensure_column(db, "recordings", "originalairdate", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "thumbnail", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "processing_status", "TEXT DEFAULT 'legacy'")
        _ensure_column(db, "recordings", "processing_percent", "INTEGER DEFAULT 0")
        _ensure_column(db, "recordings", "processing_step", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "processing_error", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "vod_ready", "INTEGER DEFAULT 0")
        _ensure_column(db, "recordings", "processed_at", "TEXT DEFAULT ''")
        _ensure_column(db, "recordings", "guide_metadata_json", "TEXT DEFAULT ''")

        db.execute("""
        CREATE TABLE IF NOT EXISTS programs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT,
            title TEXT,
            subtitle TEXT,
            description TEXT,
            start TEXT,
            stop TEXT,
            category TEXT,
            episode TEXT,
            rating TEXT,
            is_new INTEGER DEFAULT 0
        )
        """)

        db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_recordings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT,
            title TEXT,
            subtitle TEXT,
            start TEXT,
            stop TEXT,
            status TEXT DEFAULT 'Scheduled',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        db.execute("""
        CREATE TABLE IF NOT EXISTS live_program_segments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            guide_name TEXT,
            program_key TEXT NOT NULL,
            program_id TEXT,
            title TEXT,
            subtitle TEXT,
            description TEXT,
            category TEXT,
            episode TEXT,
            start_time TEXT,
            stop_time TEXT,
            first_segment TEXT,
            last_segment TEXT,
            segment_count INTEGER DEFAULT 0,
            file_path TEXT,
            status TEXT DEFAULT 'active',
            saved INTEGER DEFAULT 0,
            auto_expire INTEGER DEFAULT 1,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, program_key)
        )
        """)

        db.execute("""
        CREATE INDEX IF NOT EXISTS idx_live_program_segments_session
        ON live_program_segments(session_id, channel, status)
        """)

        # Metadata 2.0 fields. _ensure_column safely upgrades existing
        # installations without deleting or rebuilding the table.
        _ensure_column(db, "live_program_segments", "season", "INTEGER DEFAULT 0")
        _ensure_column(db, "live_program_segments", "episode_title", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "is_new", "INTEGER DEFAULT 0")
        _ensure_column(db, "live_program_segments", "originalairdate", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "show_type", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "entity_type", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "genres", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "rating", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "runtime", "INTEGER DEFAULT 0")
        _ensure_column(db, "live_program_segments", "year", "INTEGER DEFAULT 0")
        _ensure_column(db, "live_program_segments", "language", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "video_properties", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "audio_properties", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "artwork", "TEXT DEFAULT ''")
        _ensure_column(db, "live_program_segments", "expires_at", "TEXT")

        db.execute("""
        CREATE INDEX IF NOT EXISTS idx_live_program_segments_channel_time
        ON live_program_segments(channel, start_time, stop_time)
        """)

        # programs.start/stop are always stored as plain 14-char
        # YYYYMMDDHHMMSS strings, so a plain index (no substr() wrapping in
        # queries) is usable. This backs the guide screen, which otherwise
        # does a full scan of the entire programs table on every lookup.
        db.execute("""
        CREATE INDEX IF NOT EXISTS idx_programs_channel_stop
        ON programs(channel, stop)
        """)


        db.execute("""
        CREATE TABLE IF NOT EXISTS series_recordings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            channel TEXT,
            only_new INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 50,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        _ensure_column(db, "series_recordings", "priority", "INTEGER DEFAULT 50")
        _ensure_column(db, "series_recordings", "start_padding", "INTEGER DEFAULT 2")
        _ensure_column(db, "series_recordings", "end_padding", "INTEGER DEFAULT 5")
        _ensure_column(db, "series_recordings", "keep_last", "INTEGER DEFAULT 0")
        _ensure_column(db, "series_recordings", "any_channel", "INTEGER DEFAULT 0")

        _ensure_column(db, "scheduled_recordings", "priority", "INTEGER DEFAULT 50")
        _ensure_column(db, "scheduled_recordings", "start_padding", "INTEGER DEFAULT 2")
        _ensure_column(db, "scheduled_recordings", "end_padding", "INTEGER DEFAULT 5")
        _ensure_column(db, "scheduled_recordings", "series_id", "INTEGER DEFAULT 0")

        db.commit()


# --------------------------------------------------
# Recordings
# --------------------------------------------------

def list_recordings():
    with connect() as db:
        return db.execute("""
            SELECT *
            FROM recordings
            ORDER BY start_time DESC
        """).fetchall()


def add_recording(
    filename,
    channel,
    title,
    start_time,
    end_time="",
    size_bytes=0,
    status="Recording",
    subtitle="",
    description="",
    category="",
    season="",
    episode="",
    programid="",
    seriesid="",
    recording_rule=0,
    originalairdate="",
    thumbnail="",
):
    with connect() as db:
        db.execute("""
            INSERT OR REPLACE INTO recordings
            (
                filename,
                channel,
                title,
                start_time,
                end_time,
                size_bytes,
                status,
                subtitle,
                description,
                category,
                season,
                episode,
                programid,
                seriesid,
                recording_rule,
                originalairdate,
                thumbnail
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            filename,
            channel,
            title,
            start_time,
            end_time,
            int(size_bytes or 0),
            status,
            subtitle or "",
            description or "",
            category or "",
            season or "",
            episode or "",
            programid or "",
            seriesid or "",
            int(recording_rule or 0),
            originalairdate or "",
            thumbnail or "",
        ))
        db.commit()


def add_recording_from_schedule(filename, schedule, start_time, size_bytes=0, status="Recording"):
    add_recording(
        filename=filename,
        channel=schedule.get("channel", ""),
        title=schedule.get("title", ""),
        start_time=start_time,
        end_time="",
        size_bytes=size_bytes,
        status=status,
        subtitle=schedule.get("subtitle", ""),
        description=schedule.get("description", ""),
        category=schedule.get("category", ""),
        episode=schedule.get("episode", ""),
        programid=schedule.get("programid", ""),
        seriesid=schedule.get("seriesid", ""),
        recording_rule=schedule.get("series_id", 0),
        originalairdate=schedule.get("originalairdate", ""),
    )


def finish_recording(filename, end_time, size_bytes):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM recordings WHERE filename=?",
            (filename,),
        ).fetchone()

        guide_snapshot = json.dumps(dict(row), default=str) if row else ""

        db.execute("""
            UPDATE recordings
            SET end_time=?,
                size_bytes=?,
                status='Recorded',
                processing_status='pending',
                processing_percent=0,
                processing_step='Queued for playback preparation',
                processing_error='',
                vod_ready=0,
                processed_at='',
                guide_metadata_json=CASE
                    WHEN guide_metadata_json IS NULL OR guide_metadata_json=''
                    THEN ?
                    ELSE guide_metadata_json
                END
            WHERE filename=?
        """, (end_time, size_bytes, guide_snapshot, filename))
        db.commit()


def update_recording_processing(
    filename,
    status,
    percent=0,
    step="",
    error="",
    vod_ready=False,
    processed_at="",
):
    with connect() as db:
        db.execute("""
            UPDATE recordings
            SET processing_status=?,
                processing_percent=?,
                processing_step=?,
                processing_error=?,
                vod_ready=?,
                processed_at=?
            WHERE filename=?
        """, (
            status or "pending",
            max(0, min(100, int(percent or 0))),
            step or "",
            error or "",
            1 if vod_ready else 0,
            processed_at or "",
            filename,
        ))
        db.commit()


def list_recordings_needing_processing():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM recordings
            WHERE status='Recorded'
              AND COALESCE(vod_ready, 0)=0
              AND COALESCE(processing_status, 'legacy') IN ('pending', 'processing')
            ORDER BY start_time ASC
        """).fetchall()
        return [dict(row) for row in rows]


def delete_recording(filename):
    with connect() as db:
        db.execute("DELETE FROM recordings WHERE filename=?", (filename,))
        db.commit()



def delete_recording_by_id(recording_id):
    """
    Delete one recording database row by numeric ID.

    Returns the deleted recording as a dictionary so the API can remove the
    associated media file and thumbnail afterward.
    """
    with connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM recordings
            WHERE id = ?
            """,
            (int(recording_id),),
        ).fetchone()

        if not row:
            return None

        recording = dict(row)

        db.execute(
            """
            DELETE FROM recordings
            WHERE id = ?
            """,
            (int(recording_id),),
        )

        db.commit()

    return recording


def get_recordings_to_prune_for_rule(recording_rule, keep_last):
    if not recording_rule or not keep_last or int(keep_last) <= 0:
        return []

    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM recordings
            WHERE recording_rule=?
              AND status='Recorded'
            ORDER BY start_time DESC
        """, (recording_rule,)).fetchall()

        all_rows = [dict(r) for r in rows]
        return all_rows[int(keep_last):]
    

def get_recording(filename):
    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM recordings
            WHERE filename=?
        """, (filename,)).fetchone()

        return dict(row) if row else None


def get_series_recording(series_id):
    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM series_recordings
            WHERE id=?
        """, (series_id,)).fetchone()

        return dict(row) if row else None
    
def has_recorded_program(title, subtitle="", episode="", programid=""):
    with connect() as db:
        if programid:
            row = db.execute("""
                SELECT id
                FROM recordings
                WHERE programid=?
                  AND status='Recorded'
                LIMIT 1
            """, (programid,)).fetchone()
            return row is not None

        if episode:
            row = db.execute("""
                SELECT id
                FROM recordings
                WHERE title=?
                  AND episode=?
                  AND status='Recorded'
                LIMIT 1
            """, (title, episode)).fetchone()
            return row is not None

        if subtitle:
            row = db.execute("""
                SELECT id
                FROM recordings
                WHERE title=?
                  AND subtitle=?
                  AND status='Recorded'
                LIMIT 1
            """, (title, subtitle)).fetchone()
            return row is not None

        return False


# --------------------------------------------------
# Channels
# --------------------------------------------------

def list_channels():
    with connect() as db:
        return db.execute("""
            SELECT *
            FROM channels
            WHERE enabled=1
            ORDER BY guide_number
        """).fetchall()


def list_all_channels():
    """Return every configured channel, including disabled channels."""
    with connect() as db:
        return db.execute("""
            SELECT *
            FROM channels
            ORDER BY guide_number
        """).fetchall()


def set_channel_enabled(guide_number, enabled):
    """Enable or disable one configured channel without deleting it."""
    enabled_value = 1 if bool(enabled) else 0

    with connect() as db:
        cursor = db.execute("""
            UPDATE channels
            SET enabled=?
            WHERE guide_number=?
        """, (enabled_value, str(guide_number)))
        db.commit()
        return cursor.rowcount > 0


def add_channel(number, name, url=""):
    with connect() as db:
        db.execute("""
            INSERT OR REPLACE INTO channels
            (guide_number, guide_name, url)
            VALUES (?, ?, ?)
        """, (number, name, url))
        db.commit()


def upsert_channel(
    guide_number,
    guide_name,
    url="",
    source="hdhomerun",
):
    """Insert/update the HDHomeRun channel URL.

    Native DVB channel tuning comes from native_dvb_channels.conf and does
    not store a second stream URL in SQLite.
    """
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM channels WHERE guide_number=?",
            (guide_number,),
        ).fetchone()

        if existing:
            db.execute("""
                UPDATE channels
                SET guide_name=?,
                    url=?
                WHERE guide_number=?
            """, (
                guide_name,
                url,
                guide_number,
            ))
        else:
            db.execute("""
                INSERT INTO channels
                (
                    guide_number,
                    guide_name,
                    url
                )
                VALUES (?, ?, ?)
            """, (
                guide_number,
                guide_name,
                url,
            ))

        db.commit()


def _setting_enabled(key, default="0"):
    return str(
        get_setting(key, default) or default
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_channel_source(channel):
    """Choose between HDHomeRun and Native DVB only."""
    guide_number = str(
        channel["guide_number"]
        if "guide_number" in channel.keys()
        else ""
    ).strip()

    hdhr_url = str(
        channel["url"]
        if "url" in channel.keys()
        else ""
    ).strip()

    hdhr_enabled = _setting_enabled(
        "hdhomerun_enabled",
        "1",
    )
    native_enabled = _setting_enabled(
        "native_dvb_enabled",
        "0",
    )

    native_available = False

    if native_enabled and guide_number:
        try:
            from app import native_dvb
            native_available = (
                native_dvb.get_service(
                    guide_number
                )
                is not None
            )
        except Exception:
            native_available = False

    if hdhr_enabled and hdhr_url:
        if native_available:
            try:
                from app import tuner_manager

                if (
                    tuner_manager.free_count(
                        "native_dvb"
                    )
                    >
                    tuner_manager.free_count(
                        "hdhomerun"
                    )
                ):
                    return "native_dvb"
            except Exception:
                pass

        return "hdhomerun"

    if native_available:
        return "native_dvb"

    # Preserve a deterministic answer for error reporting.
    return "hdhomerun"


def get_channel(guide_number):
    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM channels
            WHERE guide_number=?
        """, (guide_number,)).fetchone()

        if not row:
            return None

        channel = dict(row)
        source = resolve_channel_source(row)

        channel["_tuner_source"] = source

        if source == "native_dvb":
            from app import native_dvb

            service = native_dvb.get_service(
                guide_number
            )

            channel["url"] = (
                f"native-dvb://{guide_number}"
                if service
                else ""
            )

            channel["_native_dvb_service"] = service
        else:
            channel["url"] = str(
                row["url"] or ""
            ).strip()

        return channel



def get_program_recording_details(program):
    """Return the current guide recording state and related rule IDs."""
    if not program:
        return {
            "status": "none",
            "recording": False,
            "series": False,
            "schedule_id": None,
            "series_id": None,
        }

    channel = str(program.get("channel") or "")
    title = str(program.get("title") or "")
    start = str(program.get("start") or "")[:14]

    with connect() as db:
        schedule = db.execute(
            """
            SELECT id, status, series_id
            FROM scheduled_recordings
            WHERE channel=?
              AND title=?
              AND substr(start, 1, 14)=?
              AND status IN ('Scheduled', 'Recording')
            ORDER BY CASE status WHEN 'Recording' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (channel, title, start),
        ).fetchone()

        series_rule = None
        if schedule and int(schedule["series_id"] or 0) > 0:
            series_rule = db.execute(
                "SELECT id FROM series_recordings WHERE id=? AND enabled=1",
                (int(schedule["series_id"]),),
            ).fetchone()

        if not series_rule:
            series_rule = db.execute(
                """
                SELECT id
                FROM series_recordings
                WHERE enabled=1
                  AND title=?
                  AND (any_channel=1 OR channel='' OR channel=?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (title, channel),
            ).fetchone()

    status = str(schedule["status"]).lower() if schedule else "none"
    return {
        "status": status,
        "recording": schedule is not None,
        "series": series_rule is not None,
        "schedule_id": int(schedule["id"]) if schedule else None,
        "series_id": int(series_rule["id"]) if series_rule else None,
    }


def get_program_recording_status(program):
    """Return none, scheduled, or recording for one exact guide airing."""
    return get_program_recording_details(program)["status"]


def get_recording_lookup_maps():
    """Preload all active scheduled/series recordings in 2 queries total.

    Used by get_guide_grid_bulk() so the full guide grid can resolve every
    program's recording/series badge in memory instead of calling
    get_program_recording_details() (1-3 queries each) per program.
    """
    with connect() as db:
        scheduled_rows = db.execute("""
            SELECT id, channel, title, start, status, series_id
            FROM scheduled_recordings
            WHERE status IN ('Scheduled', 'Recording')
        """).fetchall()

        series_rows = db.execute("""
            SELECT id, title, channel, any_channel, enabled
            FROM series_recordings
            WHERE enabled=1
        """).fetchall()

    scheduled_by_key = {}
    for row in scheduled_rows:
        key = (
            str(row["channel"]),
            str(row["title"]),
            str(row["start"] or "")[:14],
        )
        existing = scheduled_by_key.get(key)
        # Mirror the original query's ORDER BY: Recording beats Scheduled.
        if existing is None or (
            row["status"] == "Recording" and existing["status"] != "Recording"
        ):
            scheduled_by_key[key] = row

    series_by_id = {row["id"]: row for row in series_rows}

    series_by_title = {}
    for row in series_rows:
        series_by_title.setdefault(row["title"], []).append(row)

    return {
        "scheduled_by_key": scheduled_by_key,
        "series_by_id": series_by_id,
        "series_by_title": series_by_title,
    }


def resolve_recording_details(program, maps):
    """In-memory equivalent of get_program_recording_details(), using maps
    returned by get_recording_lookup_maps() instead of new DB queries."""
    if not program:
        return {
            "status": "none",
            "recording": False,
            "series": False,
            "schedule_id": None,
            "series_id": None,
        }

    channel = str(program.get("channel") or "")
    title = str(program.get("title") or "")
    start = str(program.get("start") or "")[:14]

    schedule = maps["scheduled_by_key"].get((channel, title, start))

    series_rule = None
    if schedule and int(schedule["series_id"] or 0) > 0:
        candidate = maps["series_by_id"].get(int(schedule["series_id"]))
        if candidate and candidate["enabled"]:
            series_rule = candidate

    if not series_rule:
        candidates = maps["series_by_title"].get(title, [])
        matching = [
            s for s in candidates
            if s["any_channel"] == 1 or s["channel"] in ("", channel)
        ]
        if matching:
            series_rule = max(matching, key=lambda s: s["id"])

    status = str(schedule["status"]).lower() if schedule else "none"
    return {
        "status": status,
        "recording": schedule is not None,
        "series": series_rule is not None,
        "schedule_id": int(schedule["id"]) if schedule else None,
        "series_id": int(series_rule["id"]) if series_rule else None,
    }


def get_guide_grid_bulk(
    limit_programs=200,
    window_hours=30,
    channel_offset=0,
    channel_limit=None,
):
    """Return guide rows for one ordered page of enabled channels.

    ``channel_offset`` and ``channel_limit`` let Android request only the
    first visible guide rows before fetching the rest. Program rows are also
    restricted to that channel page, so the fast initial response does not
    scan or serialize the entire lineup.
    """
    now_dt = datetime.datetime.now()
    now = now_dt.strftime("%Y%m%d%H%M%S")
    window_end = (
        now_dt + datetime.timedelta(hours=window_hours)
    ).strftime("%Y%m%d%H%M%S")

    offset = max(0, int(channel_offset or 0))
    limit = None if channel_limit is None else max(1, int(channel_limit))

    with connect() as db:
        channel_sql = """
            SELECT guide_number
            FROM channels
            WHERE enabled=1
            ORDER BY guide_number
        """
        params = []

        if limit is not None:
            channel_sql += " LIMIT ? OFFSET ?"
            params.extend((limit, offset))
        elif offset:
            # SQLite requires LIMIT when OFFSET is present. -1 means no limit.
            channel_sql += " LIMIT -1 OFFSET ?"
            params.append(offset)

        channels = db.execute(channel_sql, params).fetchall()
        channel_numbers = [str(row["guide_number"]) for row in channels]

        if not channel_numbers:
            return {}

        placeholders = ",".join("?" for _ in channel_numbers)
        program_rows = db.execute(
            f"""
            SELECT *
            FROM programs
            WHERE stop > ?
              AND start < ?
              AND channel IN ({placeholders})
            ORDER BY channel, start
            """,
            (now, window_end, *channel_numbers),
        ).fetchall()

    by_channel = {}
    for row in program_rows:
        by_channel.setdefault(str(row["channel"]), []).append(dict(row))

    return {
        number: by_channel.get(number, [])[:limit_programs]
        for number in channel_numbers
    }


# --------------------------------------------------
# Guide
# --------------------------------------------------

def get_programs():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM programs
            ORDER BY start
        """).fetchall()
        return [dict(r) for r in rows]


def get_programs_for_channel(channel, limit=30):
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM programs
            WHERE channel=?
              AND stop > ?
            ORDER BY start
            LIMIT ?
        """, (channel, now, limit)).fetchall()

        return [dict(r) for r in rows]


def get_current_program(channel, now=None):
    if now is None:
        now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM programs
            WHERE channel=?
              AND substr(start, 1, 14) <= ?
              AND substr(stop, 1, 14) > ?
            ORDER BY start
            LIMIT 1
        """, (channel, now, now)).fetchone()

        return dict(row) if row else None


def get_program_by_id(programid):
    if not programid:
        return None

    with connect() as db:
        return db.execute("""
            SELECT *
            FROM programs
            WHERE programid=?
            ORDER BY start DESC
            LIMIT 1
        """, (programid,)).fetchone()

def get_now_next():
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with connect() as db:
        channels = db.execute("""
            SELECT guide_number, guide_name
            FROM channels
            WHERE enabled=1
            ORDER BY guide_number
        """).fetchall()

        result = []

        for ch in channels:
            now_program = db.execute("""
                SELECT title, start, stop
                FROM programs
                WHERE channel=?
                  AND substr(start, 1, 14) <= ?
                  AND substr(stop, 1, 14) > ?
                ORDER BY start
                LIMIT 1
            """, (ch["guide_number"], now, now)).fetchone()

            next_program = db.execute("""
                SELECT title, start, stop
                FROM programs
                WHERE channel=?
                  AND substr(start, 1, 14) > ?
                ORDER BY start
                LIMIT 1
            """, (ch["guide_number"], now)).fetchone()

            result.append({
                "guide_number": ch["guide_number"],
                "guide_name": ch["guide_name"],
                "now_title": now_program["title"] if now_program else None,
                "now_start": now_program["start"] if now_program else None,
                "now_stop": now_program["stop"] if now_program else None,
                "next_title": next_program["title"] if next_program else None,
                "next_start": next_program["start"] if next_program else None,
                "next_stop": next_program["stop"] if next_program else None,
            })

        return result


def get_guide_grid(limit_channels=20, limit_programs=8):
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with connect() as db:
        channels = db.execute("""
            SELECT guide_number, guide_name
            FROM channels
            WHERE enabled=1
            ORDER BY guide_number
            LIMIT ?
        """, (limit_channels,)).fetchall()

        result = []

        for ch in channels:
            programs = db.execute("""
                SELECT *
                FROM programs
                WHERE channel=?
                  AND substr(stop, 1, 14) > ?
                ORDER BY start
                LIMIT ?
            """, (ch["guide_number"], now, limit_programs)).fetchall()

            result.append({
                "guide_number": ch["guide_number"],
                "guide_name": ch["guide_name"],
                "programs": [dict(p) for p in programs],
            })

        return result


# --------------------------------------------------
# Scheduled Recordings
# --------------------------------------------------

def add_scheduled_recording(
    channel,
    title,
    subtitle,
    start,
    stop,
    priority=50,
    start_padding=2,
    end_padding=5,
    series_id=0,
    description="",
    category="",
    episode="",
    programid="",
    seriesid="",
    originalairdate="",
):
    with connect() as db:
        db.execute("""
            INSERT INTO scheduled_recordings
            (
                channel,
                title,
                subtitle,
                start,
                stop,
                status,
                priority,
                start_padding,
                end_padding,
                series_id,
                description,
                category,
                episode,
                programid,
                seriesid,
                originalairdate
            )
            VALUES (
                ?, ?, ?, ?, ?, 'Scheduled',
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
        """, (
            channel or "",
            title or "",
            subtitle or "",
            start or "",
            stop or "",
            int(priority or 50),
            int(start_padding or 0),
            int(end_padding or 0),
            int(series_id or 0),
            description or "",
            category or "",
            episode or "",
            programid or "",
            seriesid or "",
            originalairdate or "",
        ))
        db.commit()


def list_scheduled_recordings():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM scheduled_recordings
            ORDER BY start
        """).fetchall()
        return [dict(r) for r in rows]


def list_upcoming_scheduled_recordings():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM scheduled_recordings
            WHERE status IN ('Scheduled','Recording')
            ORDER BY start
        """).fetchall()

        return [dict(r) for r in rows]


def list_recording_history():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM scheduled_recordings
            WHERE status NOT IN ('Scheduled','Recording')
            ORDER BY start DESC
        """).fetchall()

        return [dict(r) for r in rows]


def clear_completed_recording_history():
    """Remove successful history while keeping failed and expired entries."""
    with connect() as db:
        db.execute("""
            DELETE FROM scheduled_recordings
            WHERE TRIM(COALESCE(status, '')) IN ('Recorded', 'Completed')
        """)
        db.commit()


def clear_all_recording_history():
    """Remove every non-active recording-history entry."""
    with connect() as db:
        db.execute("""
            DELETE FROM scheduled_recordings
            WHERE TRIM(COALESCE(status, '')) NOT IN ('Scheduled', 'Recording')
        """)
        db.commit()


def clear_old_recording_history():
    """Backward-compatible alias for older callers."""
    clear_all_recording_history()


def delete_scheduled_recording(schedule_id):
    with connect() as db:
        db.execute("DELETE FROM scheduled_recordings WHERE id=?", (schedule_id,))
        db.commit()


def update_schedule_status(schedule_id, status):
    with connect() as db:
        db.execute("""
            UPDATE scheduled_recordings
            SET status=?
            WHERE id=?
        """, (status, schedule_id))
        db.commit()


def get_active_schedule():
    with connect() as db:
        return db.execute("""
            SELECT *
            FROM scheduled_recordings
            WHERE status='Recording'
            LIMIT 1
        """).fetchone()


def expire_old_scheduled_recordings(now):
    with connect() as db:
        db.execute("""
            UPDATE scheduled_recordings
            SET status='Expired'
            WHERE status='Scheduled'
              AND substr(stop, 1, 14) < ?
        """, (now,))
        db.commit()


def list_active_schedules():
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM scheduled_recordings
            WHERE status='Recording'
            ORDER BY start
        """).fetchall()

        return [dict(r) for r in rows]


def fail_scheduled_recording(schedule_id, reason):
    with connect() as db:
        db.execute("""
            UPDATE scheduled_recordings
            SET status=?
            WHERE id=?
        """, (reason, schedule_id))
        db.commit()


def recover_stale_recordings(now):
    with connect() as db:
        db.execute("""
            UPDATE scheduled_recordings
            SET status='Recorded'
            WHERE status='Recording'
              AND substr(stop, 1, 14) < ?
        """, (now,))
        db.commit()


# --------------------------------------------------
# Series Recordings
# --------------------------------------------------

def add_series_recording(
    title,
    channel="",
    only_new=0,
    priority=50,
    start_padding=2,
    end_padding=5,
    keep_last=0,
    any_channel=0,
):
    with connect() as db:
        db.execute("""
            INSERT INTO series_recordings
            (title, channel, only_new, priority, enabled, start_padding, end_padding, keep_last, any_channel)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (
            title,
            channel,
            int(only_new or 0),
            int(priority or 50),
            int(start_padding or 0),
            int(end_padding or 0),
            int(keep_last or 0),
            int(any_channel or 0),
        ))
        db.commit()


def update_series_recording(
    series_id,
    title,
    channel="",
    only_new=0,
    enabled=1,
    priority=50,
    start_padding=2,
    end_padding=5,
    keep_last=0,
    any_channel=0,
):
    with connect() as db:
        db.execute("""
            UPDATE series_recordings
            SET title=?,
                channel=?,
                only_new=?,
                enabled=?,
                priority=?,
                start_padding=?,
                end_padding=?,
                keep_last=?,
                any_channel=?
            WHERE id=?
        """, (
            title,
            channel,
            int(only_new or 0),
            int(enabled or 0),
            int(priority or 50),
            int(start_padding or 0),
            int(end_padding or 0),
            int(keep_last or 0),
            int(any_channel or 0),
            series_id,
        ))
        db.commit()


def list_series_recordings(include_disabled=False):
    with connect() as db:
        if include_disabled:
            rows = db.execute("""
                SELECT *
                FROM series_recordings
                ORDER BY enabled DESC, priority DESC, title
            """).fetchall()
        else:
            rows = db.execute("""
                SELECT *
                FROM series_recordings
                WHERE enabled=1
                ORDER BY priority DESC, title
            """).fetchall()

        return [dict(r) for r in rows]


def set_series_priority(series_id, priority):
    with connect() as db:
        db.execute("""
            UPDATE series_recordings
            SET priority=?
            WHERE id=?
        """, (priority, series_id))
        db.commit()


def delete_series_recording(series_id):
    with connect() as db:
        db.execute("DELETE FROM series_recordings WHERE id=?", (series_id,))
        db.commit()


def apply_series_rules():
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    created = 0

    with connect() as db:
        rules = db.execute("""
            SELECT *
            FROM series_recordings
            WHERE enabled=1
            ORDER BY priority DESC, title
        """).fetchall()

        for rule in rules:
            only_new_clause = (
                "AND COALESCE(is_new, 0)=1"
                if int(rule["only_new"] or 0)
                else ""
            )
            record_any_channel = int(rule["any_channel"] or 0) == 1
            has_channel = bool(rule["channel"])

            if has_channel and not record_any_channel:
                programs = db.execute(f"""
                    SELECT *
                    FROM programs
                    WHERE title=?
                      AND channel=?
                      AND substr(stop, 1, 14) > ?
                      {only_new_clause}
                    ORDER BY start
                """, (rule["title"], rule["channel"], now)).fetchall()
            else:
                programs = db.execute(f"""
                    SELECT *
                    FROM programs
                    WHERE title=?
                      AND substr(stop, 1, 14) > ?
                      {only_new_clause}
                    ORDER BY start
                """, (rule["title"], now)).fetchall()

            for program in programs:
                if has_recorded_program(
                    title=program["title"],
                    subtitle=program["subtitle"] or "",
                    episode=program["episode"] or "",
                    programid="",
                ):
                    continue

                existing = db.execute("""
                    SELECT id
                    FROM scheduled_recordings
                    WHERE channel=?
                      AND title=?
                      AND substr(start, 1, 14)=substr(?, 1, 14)
                    LIMIT 1
                """, (
                    program["channel"],
                    program["title"],
                    program["start"],
                )).fetchone()

                if existing:
                    db.execute("""
                        UPDATE scheduled_recordings
                        SET priority=?,
                            start_padding=?,
                            end_padding=?,
                            series_id=?,
                            subtitle=?,
                            description=?,
                            category=?,
                            episode=?,
                            programid=?,
                            seriesid=?,
                            originalairdate=?
                        WHERE id=?
                          AND status='Scheduled'
                    """, (
                        int(rule["priority"] or 50),
                        int(rule["start_padding"] or 0),
                        int(rule["end_padding"] or 0),
                        int(rule["id"]),
                        program["subtitle"] or "",
                        program["description"] or "",
                        program["category"] or "",
                        program["episode"] or "",
                        program["programid"] or "",
                        program["seriesid"] or "",
                        program["originalairdate"] or "",
                        existing["id"],
                    ))
                    continue

                db.execute("""
                    INSERT INTO scheduled_recordings
                    (
                        channel,
                        title,
                        subtitle,
                        start,
                        stop,
                        status,
                        priority,
                        start_padding,
                        end_padding,
                        series_id,
                        description,
                        category,
                        episode,
                        programid,
                        seriesid,
                        originalairdate
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, 'Scheduled',
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                """, (
                    program["channel"],
                    program["title"],
                    program["subtitle"] or "",
                    program["start"],
                    program["stop"],
                    int(rule["priority"] or 50),
                    int(rule["start_padding"] or 0),
                    int(rule["end_padding"] or 0),
                    int(rule["id"]),
                    program["description"] or "",
                    program["category"] or "",
                    program["episode"] or "",
                    program["programid"] or "",
                    program["seriesid"] or "",
                    program["originalairdate"] or "",
                ))
                created += 1

        db.commit()

    return created



def get_guide_program(program_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM programs WHERE id=? LIMIT 1",
            (int(program_id),),
        ).fetchone()
        return dict(row) if row else None


def get_scheduled_recording(schedule_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM scheduled_recordings WHERE id=? LIMIT 1",
            (int(schedule_id),),
        ).fetchone()
        return dict(row) if row else None


def schedule_guide_program_once(program_id):
    program = get_guide_program(program_id)
    if not program:
        return None

    details = get_program_recording_details(program)
    if details["schedule_id"]:
        return {"program": program, **details}

    add_scheduled_recording(
        channel=program.get("channel", ""),
        title=program.get("title", ""),
        subtitle=program.get("subtitle", ""),
        start=program.get("start", ""),
        stop=program.get("stop", ""),
        priority=int(get_setting("default_priority", "50") or 50),
        start_padding=int(get_setting("default_start_padding", "2") or 2),
        end_padding=int(get_setting("default_end_padding", "5") or 5),
        series_id=0,
        description=program.get("description", ""),
        category=program.get("category", ""),
        episode=program.get("episode", ""),
        programid=program.get("programid", ""),
        seriesid=program.get("seriesid", ""),
        originalairdate=program.get("originalairdate", ""),
    )
    return {"program": program, **get_program_recording_details(program)}


def create_guide_series_rule(program_id, only_new=0):
    program = get_guide_program(program_id)
    if not program:
        return None

    title = str(program.get("title") or "")
    channel = str(program.get("channel") or "")

    with connect() as db:
        existing = db.execute(
            """
            SELECT id FROM series_recordings
            WHERE enabled=1 AND title=?
              AND (any_channel=1 OR channel='' OR channel=?)
            ORDER BY id DESC LIMIT 1
            """,
            (title, channel),
        ).fetchone()

    if not existing:
        add_series_recording(
            title=title,
            channel=channel,
            only_new=1 if only_new else 0,
            priority=int(get_setting("default_priority", "50") or 50),
            start_padding=int(get_setting("default_start_padding", "2") or 2),
            end_padding=int(get_setting("default_end_padding", "5") or 5),
            keep_last=int(get_setting("default_keep_last", "0") or 0),
            any_channel=0,
        )

    apply_series_rules()
    return {"program": program, **get_program_recording_details(program)}


def cancel_guide_program_schedule(program_id):
    program = get_guide_program(program_id)
    if not program:
        return None

    details = get_program_recording_details(program)
    schedule_id = details.get("schedule_id")
    if not schedule_id:
        return {"program": program, **details}

    schedule = get_scheduled_recording(schedule_id)
    if schedule and schedule.get("status") == "Scheduled":
        delete_scheduled_recording(schedule_id)

    return {
        "program": program,
        "cancelled_schedule_id": schedule_id,
        **get_program_recording_details(program),
    }


def delete_series_rule_and_future_schedules(series_id):
    with connect() as db:
        db.execute(
            "DELETE FROM scheduled_recordings WHERE series_id=? AND status='Scheduled'",
            (int(series_id),),
        )
        db.execute("DELETE FROM series_recordings WHERE id=?", (int(series_id),))
        db.commit()


# --------------------------------------------------
# Conflicts
# --------------------------------------------------

def _schedule_window(row):
    start = str(row["start"])[:14]
    stop = str(row["stop"])[:14]

    try:
        start_dt = _parse_xmltv_time(row["start"]) - datetime.timedelta(minutes=int(row.get("start_padding", 0) or 0))
        stop_dt = _parse_xmltv_time(row["stop"]) + datetime.timedelta(minutes=int(row.get("end_padding", 0) or 0))
        start = start_dt.strftime("%Y%m%d%H%M%S")
        stop = stop_dt.strftime("%Y%m%d%H%M%S")
    except Exception:
        pass

    return start, stop


def get_schedule_conflicts(max_tuners=4):
    rows = list_scheduled_recordings()

    active_rows = [
        r for r in rows
        if r["status"] in ("Scheduled", "Recording")
    ]

    conflicts = []

    for r in active_rows:
        overlaps = []
        r_start, r_stop = _schedule_window(r)

        for other in active_rows:
            o_start, o_stop = _schedule_window(other)

            if o_start < r_stop and o_stop > r_start:
                overlaps.append(other)

        if len(overlaps) > max_tuners:
            conflicts.append({
                "recording": r,
                "count": len(overlaps),
                "max_tuners": max_tuners,
                "overlap": overlaps,
            })

    return conflicts


# --------------------------------------------------
# Guide-aware live buffer program segments
# --------------------------------------------------

def live_program_key(channel, program=None):
    """
    Stable key for one specific guide airing.

    A program ID alone is not sufficient because recurring programs, local
    news, and generic guide entries may reuse the same program ID across
    multiple time slots. Include the scheduled start and stop times so every
    airing gets its own live-buffer program row and Background DVR folder.
    """
    program = program or {}

    program_id = (
        program.get("programid")
        or program.get("program_id")
        or ""
    )

    start = (
        program.get("start")
        or program.get("start_time")
        or ""
    )

    stop = (
        program.get("stop")
        or program.get("stop_time")
        or ""
    )

    title = program.get("title") or "Unknown Program"

    return f"{channel}:{program_id}:{start}:{stop}:{title}"


def get_active_live_program_segment(session_id, channel):
    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE session_id=?
              AND channel=?
              AND status='active'
            ORDER BY id DESC
            LIMIT 1
        """, (session_id, channel)).fetchone()

        return dict(row) if row else None


def open_live_program_segment(
    session_id,
    channel,
    guide_name="",
    program=None,
    first_segment="",
    last_segment="",
    segment_count=0,
    file_path="",
):
    program = program or {}
    key = live_program_key(channel, program)

    program_id = (
        program.get("programid")
        or program.get("program_id")
        or ""
    )

    title = program.get("title") or "Unknown Program"
    subtitle = program.get("subtitle") or ""
    description = program.get("description") or ""
    category = program.get("category") or ""
    episode = program.get("episode") or ""

    try:
        season = int(program.get("season") or 0)
    except (TypeError, ValueError):
        season = 0

    episode_title = (
        program.get("episode_title")
        or program.get("episodeTitle150")
        or subtitle
        or ""
    )

    is_new_value = program.get("is_new")

    if is_new_value is None:
        is_new_value = program.get("new")

    try:
        is_new = 1 if int(is_new_value or 0) else 0
    except (TypeError, ValueError):
        is_new = 1 if bool(is_new_value) else 0

    originalairdate = (
        program.get("originalairdate")
        or program.get("original_air_date")
        or program.get("originalAirDate")
        or ""
    )

    show_type = (
        program.get("show_type")
        or program.get("showType")
        or ""
    )

    entity_type = (
        program.get("entity_type")
        or program.get("entityType")
        or ""
    )

    genres = program.get("genres") or ""
    rating = program.get("rating") or ""

    try:
        runtime = int(program.get("runtime") or 0)
    except (TypeError, ValueError):
        runtime = 0

    try:
        year = int(program.get("year") or 0)
    except (TypeError, ValueError):
        year = 0

    language = program.get("language") or ""
    video_properties = program.get("video_properties") or ""
    audio_properties = program.get("audio_properties") or ""
    artwork = program.get("artwork") or ""

    start_time = (
        program.get("start")
        or program.get("start_time")
        or ""
    )

    stop_time = (
        program.get("stop")
        or program.get("stop_time")
        or ""
    )

    with connect() as db:
        db.execute("""
            INSERT OR IGNORE INTO live_program_segments(
                session_id,
                channel,
                guide_name,
                program_key,
                program_id,
                title,
                subtitle,
                description,
                category,
                episode,
                season,
                episode_title,
                is_new,
                originalairdate,
                show_type,
                entity_type,
                genres,
                rating,
                runtime,
                year,
                language,
                video_properties,
                audio_properties,
                artwork,
                start_time,
                stop_time,
                first_segment,
                last_segment,
                segment_count,
                file_path,
                status,
                saved,
                auto_expire,
                started_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'active', 0, 1,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """, (
            session_id,
            channel,
            guide_name or "",
            key,
            program_id,
            title,
            subtitle,
            description,
            category,
            episode,
            season,
            episode_title,
            is_new,
            originalairdate,
            show_type,
            entity_type,
            genres,
            rating,
            runtime,
            year,
            language,
            video_properties,
            audio_properties,
            artwork,
            start_time,
            stop_time,
            first_segment or "",
            last_segment or first_segment or "",
            int(segment_count or 0),
            file_path or "",
        ))

        # Refresh metadata as well as segment progress. This also upgrades a
        # row created earlier with incomplete guide information.
        db.execute("""
            UPDATE live_program_segments
            SET guide_name=COALESCE(NULLIF(?, ''), guide_name),
                program_id=COALESCE(NULLIF(?, ''), program_id),
                title=COALESCE(NULLIF(?, ''), title),
                subtitle=COALESCE(NULLIF(?, ''), subtitle),
                description=COALESCE(NULLIF(?, ''), description),
                category=COALESCE(NULLIF(?, ''), category),
                episode=COALESCE(NULLIF(?, ''), episode),
                season=CASE WHEN ? > 0 THEN ? ELSE season END,
                episode_title=COALESCE(NULLIF(?, ''), episode_title),
                is_new=?,
                originalairdate=COALESCE(NULLIF(?, ''), originalairdate),
                show_type=COALESCE(NULLIF(?, ''), show_type),
                entity_type=COALESCE(NULLIF(?, ''), entity_type),
                genres=COALESCE(NULLIF(?, ''), genres),
                rating=COALESCE(NULLIF(?, ''), rating),
                runtime=CASE WHEN ? > 0 THEN ? ELSE runtime END,
                year=CASE WHEN ? > 0 THEN ? ELSE year END,
                language=COALESCE(NULLIF(?, ''), language),
                video_properties=COALESCE(
                    NULLIF(?, ''),
                    video_properties
                ),
                audio_properties=COALESCE(
                    NULLIF(?, ''),
                    audio_properties
                ),
                artwork=COALESCE(NULLIF(?, ''), artwork),
                start_time=COALESCE(NULLIF(?, ''), start_time),
                stop_time=COALESCE(NULLIF(?, ''), stop_time),
                last_segment=COALESCE(NULLIF(?, ''), last_segment),
                segment_count=MAX(segment_count, ?),
                status=CASE
                    WHEN saved=0 AND status='buffered' THEN 'active'
                    ELSE status
                END,
                ended_at=CASE
                    WHEN saved=0 AND status='buffered' THEN NULL
                    ELSE ended_at
                END,
                expires_at=CASE
                    WHEN saved=0 AND status='buffered' THEN NULL
                    ELSE expires_at
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE session_id=?
              AND program_key=?
        """, (
            guide_name or "",
            program_id,
            title,
            subtitle,
            description,
            category,
            episode,
            season,
            season,
            episode_title,
            is_new,
            originalairdate,
            show_type,
            entity_type,
            genres,
            rating,
            runtime,
            runtime,
            year,
            year,
            language,
            video_properties,
            audio_properties,
            artwork,
            start_time,
            stop_time,
            last_segment or first_segment or "",
            int(segment_count or 0),
            session_id,
            key,
        ))

        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE session_id=?
              AND program_key=?
            LIMIT 1
        """, (
            session_id,
            key,
        )).fetchone()

        db.commit()
        return dict(row) if row else None


def update_live_program_segment_bounds(segment_id, first_segment="", last_segment="", segment_count=0):
    """
    Repair/update the exact segment range for a live program row.

    This is used when the livebuffer folder was reset but the DB still had an
    active row with an old first_segment from a previous session.
    """
    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET first_segment=COALESCE(NULLIF(?, ''), first_segment),
                last_segment=COALESCE(NULLIF(?, ''), last_segment),
                segment_count=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            first_segment or "",
            last_segment or "",
            int(segment_count or 0),
            int(segment_id),
        ))
        db.commit()


def set_live_program_segment_file_path(segment_id, file_path):
    """Point a catalog row at its dedicated per-program HLS folder."""
    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET file_path=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (str(file_path or ""), int(segment_id)))
        db.commit()


def update_live_program_segment_progress(segment_id, last_segment="", segment_count=0):
    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET last_segment=COALESCE(NULLIF(?, ''), last_segment),
                segment_count=MAX(segment_count, ?),
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (last_segment or "", int(segment_count or 0), int(segment_id)))
        db.commit()


def _background_dvr_expiration():
    """
    Calculate expiration for a completed, unsaved Background DVR program.

    Expiration timestamps are stored in UTC to match SQLite
    CURRENT_TIMESTAMP.
    """
    retention = str(
        get_setting("background_dvr_retention", "end_of_day")
        or "end_of_day"
    ).strip().lower()

    allowed_retention = {
        "end_of_day",
        "1_day",
        "3_days",
        "7_days",
        "14_days",
        "30_days",
        "never",
    }

    if retention not in allowed_retention:
        retention = "end_of_day"

    if retention == "never":
        return None, 0

    now_local = datetime.datetime.now().astimezone()

    if retention == "end_of_day":
        tomorrow_local = (
            now_local + datetime.timedelta(days=1)
        ).date()

        expiration_local = datetime.datetime.combine(
            tomorrow_local,
            datetime.time.min,
            tzinfo=now_local.tzinfo,
        )
    else:
        retention_days = {
            "1_day": 1,
            "3_days": 3,
            "7_days": 7,
            "14_days": 14,
            "30_days": 30,
        }

        expiration_local = now_local + datetime.timedelta(
            days=retention_days[retention]
        )

    expiration_utc = expiration_local.astimezone(
        datetime.timezone.utc
    )

    return (
        expiration_utc.strftime("%Y-%m-%d %H:%M:%S"),
        1,
    )


def close_live_program_segment(
    segment_id,
    last_segment="",
    segment_count=0,
    status="buffered",
):
    """
    Finalize a Background DVR program.

    Unsaved completed programs receive an expiration time based on the
    background_dvr_retention setting. Saved programs never expire.

    Background DVR retention is independent from the live rewind buffer.
    """
    normalized_status = str(
        status or "buffered"
    ).strip().lower()

    expires_at = None
    retention_auto_expire = 1

    if normalized_status == "buffered":
        expires_at, retention_auto_expire = (
            _background_dvr_expiration()
        )

    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET status=CASE
                    WHEN saved=1 THEN 'saved'
                    ELSE ?
                END,
                last_segment=COALESCE(NULLIF(?, ''), last_segment),
                segment_count=MAX(segment_count, ?),
                ended_at=CURRENT_TIMESTAMP,
                auto_expire=CASE
                    WHEN saved=1 THEN 0
                    WHEN auto_expire=0 THEN 0
                    WHEN ?='buffered' THEN ?
                    ELSE auto_expire
                END,
                expires_at=CASE
                    WHEN saved=1 THEN NULL
                    WHEN auto_expire=0 THEN NULL
                    WHEN ?='buffered' THEN ?
                    ELSE expires_at
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            normalized_status,
            last_segment or "",
            int(segment_count or 0),
            normalized_status,
            int(retention_auto_expire),
            normalized_status,
            expires_at,
            int(segment_id),
        ))
        db.commit()




def get_background_dvr_settings():
    """Return validated Background DVR appliance settings."""
    retention = str(get_setting("background_dvr_retention", "end_of_day") or "end_of_day").strip().lower()
    if retention not in {"end_of_day", "1_day", "3_days", "7_days", "14_days", "30_days", "never"}:
        retention = "end_of_day"

    when_full = str(get_setting("background_dvr_when_full", "delete_oldest") or "delete_oldest").strip().lower()
    if when_full not in {"delete_oldest", "stop_buffering"}:
        when_full = "delete_oldest"

    try:
        storage_limit_gb = max(0, int(float(get_setting("background_dvr_storage_limit_gb", "250") or 250)))
    except (TypeError, ValueError):
        storage_limit_gb = 250

    def enabled(key, default="1"):
        return str(get_setting(key, default) or default).strip().lower() in {"1", "true", "yes", "on"}

    return {
        "enabled": enabled("background_dvr_enabled"),
        "retention": retention,
        "storage_limit_gb": storage_limit_gb,
        "when_full": when_full,
        "keep_saved": enabled("background_dvr_keep_saved"),
        "show_library": enabled("background_dvr_show_library"),
    }


def list_background_dvr_storage_candidates(limit=5000):
    """Oldest unsaved completed programs eligible for quota cleanup."""
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE status='buffered'
              AND saved=0
            ORDER BY COALESCE(ended_at, updated_at, started_at) ASC, id ASC
            LIMIT ?
        """, (max(1, int(limit or 5000)),)).fetchall()
        return [dict(row) for row in rows]

def list_expired_live_program_segments(limit=500):
    """
    Return unsaved Background DVR rows whose explicit expires_at time has
    passed. The cleanup service decides whether the associated file_path is
    a dedicated program folder or a legacy shared-session path.
    """
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE status='buffered'
              AND saved=0
              AND auto_expire=1
              AND NULLIF(expires_at, '') IS NOT NULL
              AND expires_at <= CURRENT_TIMESTAMP
            ORDER BY expires_at ASC, id ASC
            LIMIT ?
        """, (max(1, int(limit or 500)),)).fetchall()

        return [dict(row) for row in rows]


def delete_expired_live_program_segment(segment_id):
    """
    Delete one expired, unsaved Background DVR row.

    The expiration conditions are repeated here so a row that becomes saved
    between selection and deletion cannot accidentally be removed.
    """
    with connect() as db:
        cursor = db.execute("""
            DELETE FROM live_program_segments
            WHERE id=?
              AND status='buffered'
              AND saved=0
              AND auto_expire=1
              AND NULLIF(expires_at, '') IS NOT NULL
              AND expires_at <= CURRENT_TIMESTAMP
        """, (int(segment_id),))
        db.commit()
        return int(cursor.rowcount or 0)


def list_live_program_segments(session_id=None, channel=None, limit=50):
    sql = """
        SELECT *
        FROM live_program_segments
        WHERE 1=1
    """
    args = []

    if session_id:
        sql += " AND session_id=?"
        args.append(session_id)

    if channel:
        sql += " AND channel=?"
        args.append(channel)

    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit or 50))

    with connect() as db:
        rows = db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def get_live_program_segment(segment_id):
    with connect() as db:
        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE id=?
            LIMIT 1
        """, (int(segment_id),)).fetchone()
        return dict(row) if row else None


def list_program_catalog(status="", saved=None, channel="", limit=100):
    """
    List the guide-aware live-buffer catalog.

    This is the first Program Catalog API surface: every watched live program
    is represented as a row that can be buffered, saved, recorded, or archived.
    """
    sql = """
        SELECT *
        FROM live_program_segments
        WHERE 1=1
    """
    args = []

    if status:
        sql += " AND status=?"
        args.append(str(status))

    if saved is not None:
        sql += " AND saved=?"
        args.append(1 if saved else 0)

    if channel:
        sql += " AND channel=?"
        args.append(str(channel))

    sql += """
        ORDER BY
            CASE status
                WHEN 'active' THEN 0
                WHEN 'saved' THEN 1
                WHEN 'recorded' THEN 2
                WHEN 'buffered' THEN 3
                ELSE 4
            END,
            COALESCE(NULLIF(start_time, ''), created_at) DESC,
            id DESC
        LIMIT ?
    """
    args.append(int(limit or 100))

    with connect() as db:
        rows = db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]



def get_background_dvr_stats():
    """Return lightweight lifecycle counts for Background DVR health checks."""
    with connect() as db:
        row = db.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status='buffered' AND saved=0 THEN 1 ELSE 0 END) AS buffered,
                SUM(CASE WHEN saved=1 OR status='saved' THEN 1 ELSE 0 END) AS saved,
                SUM(CASE
                    WHEN status='buffered'
                     AND saved=0
                     AND auto_expire=1
                     AND NULLIF(expires_at, '') IS NOT NULL
                    THEN 1 ELSE 0 END
                ) AS expirable,
                SUM(CASE
                    WHEN status='buffered'
                     AND saved=0
                     AND auto_expire=1
                     AND NULLIF(expires_at, '') IS NOT NULL
                     AND expires_at <= CURRENT_TIMESTAMP
                    THEN 1 ELSE 0 END
                ) AS expired
            FROM live_program_segments
        """).fetchone()

        return {
            "total": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "buffered": int(row["buffered"] or 0),
            "saved": int(row["saved"] or 0),
            "expirable": int(row["expirable"] or 0),
            "expired": int(row["expired"] or 0),
        }


def mark_live_program_segment_saved(segment_id):
    """
    Preserve an existing Background DVR program permanently.

    Saving does not copy or move media. It only disables expiration for the
    existing program folder.
    """
    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET saved=1,
                auto_expire=0,
                expires_at=NULL,
                status=CASE
                    WHEN status='active' THEN 'active'
                    ELSE 'saved'
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (int(segment_id),))

        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE id=?
            LIMIT 1
        """, (int(segment_id),)).fetchone()

        db.commit()
        return dict(row) if row else None



def update_live_program_segment_media(segment_id, file_path, first_segment, last_segment, segment_count, status="saved", saved=1, auto_expire=0):
    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET file_path=?,
                first_segment=?,
                last_segment=?,
                segment_count=?,
                status=?,
                saved=?,
                auto_expire=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            str(file_path or ""),
            first_segment or "",
            last_segment or "",
            int(segment_count or 0),
            status or "saved",
            int(saved or 0),
            int(auto_expire or 0),
            int(segment_id),
        ))

        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE id=?
            LIMIT 1
        """, (int(segment_id),)).fetchone()

        db.commit()
        return dict(row) if row else None


def delete_live_program_segment(segment_id):
    """
    Delete one non-active Background DVR catalog row.

    Media deletion is handled and validated by program_catalog.delete_program()
    before this function is called.
    """
    with connect() as db:
        cursor = db.execute("""
            DELETE FROM live_program_segments
            WHERE id=?
              AND status != 'active'
        """, (int(segment_id),))

        db.commit()
        return int(cursor.rowcount or 0)



def archive_stale_buffered_segments(retention_seconds=None):
    """
    Legacy compatibility stub.

    Background DVR no longer uses an archived state. Expired media will be
    deleted by the cleanup service using the expires_at column.

    Keep this function temporarily so older cleanup_service code does not fail
    while the expiration cleanup is installed.
    """
    return 0


def mark_live_program_segment_expirable(segment_id, retention_seconds=None):
    """
    Return a saved program to Background DVR auto-expiration.

    retention_seconds is supplied by the existing local SignalDVR settings.
    No guide provider or Schedules Direct request is performed here.
    """
    if retention_seconds is None:
        # Lazy import avoids module import-order problems.
        from app.stream_engine import manager as stream_engine
        retention_seconds = stream_engine.live_buffer_retention_seconds()

    retention_seconds = max(0, int(retention_seconds or 0))
    expiry_modifier = f"+{retention_seconds} seconds"

    with connect() as db:
        db.execute("""
            UPDATE live_program_segments
            SET saved=0,
                auto_expire=1,
                expires_at=datetime('now', ?),
                status=CASE
                    WHEN status='saved' THEN 'buffered'
                    ELSE status
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            expiry_modifier,
            int(segment_id),
        ))

        row = db.execute("""
            SELECT *
            FROM live_program_segments
            WHERE id=?
            LIMIT 1
        """, (int(segment_id),)).fetchone()

        db.commit()
        return dict(row) if row else None
