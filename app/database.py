import sqlite3
import datetime
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB = BASE_DIR / "database" / "signaldvr.db"


def connect():
    conn = sqlite3.connect(DB, timeout=30)
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
    with connect() as db:
        rows = db.execute("""
            SELECT *
            FROM sd_lineups
            ORDER BY name
        """).fetchall()

        return [dict(r) for r in rows]


def list_sd_lineups():
    with connect() as db:
        rows = db.execute("""
        SELECT *
        FROM sd_lineups
        ORDER BY name
        """).fetchall()

        return [dict(r) for r in rows]

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
        CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guide_number TEXT UNIQUE,
            guide_name TEXT,
            url TEXT,
            favorite INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        )
        """)

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
        db.execute("""
            UPDATE recordings
            SET end_time=?, size_bytes=?, status='Recorded'
            WHERE filename=?
        """, (end_time, size_bytes, filename))
        db.commit()


def delete_recording(filename):
    with connect() as db:
        db.execute("DELETE FROM recordings WHERE filename=?", (filename,))
        db.commit()


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


def add_channel(number, name, url=""):
    with connect() as db:
        db.execute("""
            INSERT OR REPLACE INTO channels
            (guide_number, guide_name, url)
            VALUES (?, ?, ?)
        """, (number, name, url))
        db.commit()


def get_channel(guide_number):
    with connect() as db:
        return db.execute("""
            SELECT *
            FROM channels
            WHERE guide_number=?
        """, (guide_number,)).fetchone()


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
              AND substr(stop, 1, 14) > ?
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
):
    with connect() as db:
        db.execute("""
            INSERT INTO scheduled_recordings
            (channel, title, subtitle, start, stop, status, priority, start_padding, end_padding, series_id)
            VALUES (?, ?, ?, ?, ?, 'Scheduled', ?, ?, ?, ?)
        """, (
            channel,
            title,
            subtitle,
            start,
            stop,
            int(priority or 50),
            int(start_padding or 0),
            int(end_padding or 0),
            int(series_id or 0),
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


def clear_old_recording_history():
    with connect() as db:
        db.execute("""
            DELETE FROM scheduled_recordings
            WHERE status NOT IN ('Scheduled','Recording')
        """)
        db.commit()


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

    with connect() as db:
        rules = db.execute("""
            SELECT *
            FROM series_recordings
            WHERE enabled=1
            ORDER BY priority DESC, title
        """).fetchall()

        created = 0

        for rule in rules:
            only_new_clause = "AND COALESCE(is_new, 0)=1" if int(rule["only_new"] or 0) else ""
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

        for p in programs:

            if has_recorded_program(
                 title=p["title"],
                 subtitle=p["subtitle"] or "",
                 episode=p["episode"] or "",
                 programid="",
             
            ):
                    print(
                        "Skipping already recorded:",
                        p["title"],
                        p["episode"] or "",
                        p["subtitle"] or "",
                        flush=True,
                    )
                    continue

            exists = db.execute("""
                       SELECT id
                       FROM scheduled_recordings
                       WHERE channel=?
                       AND title=?
                       AND start=?
                """, (p["channel"], p["title"], p["start"])).fetchone()

            if exists:
                    db.execute("""
                        UPDATE scheduled_recordings
                        SET priority=?,
                            start_padding=?,
                            end_padding=?,
                            series_id=?
                        WHERE id=?
                          AND status='Scheduled'
                    """, (
                        int(rule["priority"] or 50),
                        int(rule["start_padding"] or 0),
                        int(rule["end_padding"] or 0),
                        int(rule["id"]),
                        exists["id"],
                    ))
                    continue

            db.execute("""
                                 INSERT INTO scheduled_recordings
                    (
                        channel, title, subtitle, start, stop, status,
                        priority, start_padding, end_padding, series_id,
                        description, category, episode, programid, seriesid, originalairdate
                    )
                    VALUES (?, ?, ?, ?, ?, 'Scheduled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    p["channel"],
                    p["title"],
                    p["subtitle"] or "",
                    p["start"],
                    p["stop"],
                    int(rule["priority"] or 50),
                    int(rule["start_padding"] or 0),
                    int(rule["end_padding"] or 0),
                    int(rule["id"]),
                    p["description"] or "",
                    p["category"] or "",
                    p["episode"] or "",
                    "",
                    "",
                    "",
                ))

        created += 1

        db.commit()
        return created


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
