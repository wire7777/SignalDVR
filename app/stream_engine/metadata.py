import json
from datetime import datetime

from app import database
from app.stream_engine import index


def current_program(channel):
    """
    Return the current guide entry for a channel.
    """
    now = datetime.now().strftime("%Y%m%d%H%M%S")

    with database.connect() as db:
        row = db.execute("""
            SELECT *
            FROM programs
            WHERE channel=?
              AND start<=?
              AND stop>?
            LIMIT 1
        """, (channel, now, now)).fetchone()

    return dict(row) if row else None


def attach_program(session_dir, channel):
    """
    Attach current guide metadata to every segment
    that doesn't already have it.
    """
    rows = index.load_index(session_dir)

    if not rows:
        return

    program = current_program(channel)

    if not program:
        return

    changed = False

    for row in rows:
        if "program_id" in row:
            continue

        row["program_id"] = program.get("programid")
        row["title"] = program.get("title")
        row["subtitle"] = program.get("subtitle")
        row["description"] = program.get("description")
        row["start"] = program.get("start")
        row["stop"] = program.get("stop")
        changed = True

    if changed:
        index.save_index(session_dir, rows)
