import sqlite3
from app import config

DB = config.BASE / "database" / "andresdvr.db"


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as db:
        db.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            size_bytes INTEGER DEFAULT 0,
            status TEXT NOT NULL
        )
        """)

        db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guide_number TEXT NOT NULL UNIQUE,
            guide_name TEXT,
            url TEXT,
            favorite INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        )
        """)

        db.commit()


def add_recording(filename, channel, title, start_time):
    with connect() as db:
        db.execute("""
        INSERT OR IGNORE INTO recordings
        (filename, channel, title, start_time, status)
        VALUES (?, ?, ?, ?, 'recording')
        """, (filename, channel, title, start_time))
        db.commit()


def finish_recording(filename, end_time, size_bytes):
    with connect() as db:
        db.execute("""
        UPDATE recordings
        SET end_time=?, size_bytes=?, status='recorded'
        WHERE filename=?
        """, (end_time, size_bytes, filename))
        db.commit()


def list_recordings():
    with connect() as db:
        return db.execute("""
        SELECT *
        FROM recordings
        ORDER BY start_time DESC
        """).fetchall()


def delete_recording(filename):
    with connect() as db:
        db.execute("DELETE FROM recordings WHERE filename=?", (filename,))
        db.commit()


def upsert_channel(guide_number, guide_name, url):
    with connect() as db:
        db.execute("""
        INSERT INTO channels (guide_number, guide_name, url)
        VALUES (?, ?, ?)
        ON CONFLICT(guide_number) DO UPDATE SET
            guide_name=excluded.guide_name,
            url=excluded.url
        """, (guide_number, guide_name, url))
        db.commit()


def list_channels():
    with connect() as db:
        return db.execute("""
        SELECT *
        FROM channels
        WHERE enabled=1
        ORDER BY CAST(guide_number AS REAL)
        """).fetchall()


def get_channel(guide_number):
    with connect() as db:
        return db.execute("""
        SELECT *
        FROM channels
        WHERE guide_number=?
        """, (guide_number,)).fetchone()
