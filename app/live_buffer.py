import datetime
import json
import shutil
import subprocess
import time
from pathlib import Path

from app import config, database


SESSIONS_DIR = config.LIVEBUFFER / "sessions"
ACTIVE_FILE = config.LIVEBUFFER / "active_live_session.json"


def _now_id():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_channel(channel):
    return str(channel).replace(".", "_").replace("/", "_")


def get_current_program(channel):
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with database.connect() as db:
        row = db.execute("""
            SELECT *
            FROM programs
            WHERE channel=?
              AND start <= ?
              AND stop > ?
            ORDER BY start
            LIMIT 1
        """, (channel, now, now)).fetchone()

        return dict(row) if row else None


def _pid_running(pid):
    if not pid:
        return False

    try:
        subprocess.run(["kill", "-0", str(pid)], check=True)
        return True
    except Exception:
        return False


def get_active_session():
    if not ACTIVE_FILE.exists():
        return None

    try:
        return json.loads(ACTIVE_FILE.read_text())
    except Exception:
        return None


def stop_active_session():
    active = get_active_session()

    if not active:
        ACTIVE_FILE.unlink(missing_ok=True)
        return True

    pid = active.get("pid")

    if pid and _pid_running(pid):
        subprocess.run(["kill", str(pid)], check=False)

        for _ in range(20):
            if not _pid_running(pid):
                break
            time.sleep(0.2)

        if _pid_running(pid):
            subprocess.run(["kill", "-9", str(pid)], check=False)

    ACTIVE_FILE.unlink(missing_ok=True)
    return True


def start_session(channel):
    ch = database.get_channel(channel)

    if not ch:
        raise RuntimeError("Channel not found")

    active = get_active_session()

    if active and active.get("channel") == ch["guide_number"]:
        playlist = Path(active.get("session_dir", "")) / "live.m3u8"

        if playlist.exists() and _pid_running(active.get("pid")):
            return active

    stop_active_session()

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    session_id = f"{_now_id()}_{_safe_channel(channel)}"
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    playlist = session_dir / "live.m3u8"
    segment_pattern = session_dir / "segment_%06d.ts"

    program = get_current_program(channel)

    metadata = {
        "session_id": session_id,
        "channel": ch["guide_number"],
        "guide_name": ch["guide_name"],
        "url": ch["url"],
        "program": program,
        "started_at": datetime.datetime.now().isoformat(),
        "playlist": f"/livebuffer/sessions/{session_id}/live.m3u8",
    }

    (session_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    cmd = [
        "ffmpeg",
        "-y",
        "-i", ch["url"],
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_flags", "append_list+program_date_time",
        "-hls_segment_filename", str(segment_pattern),
        str(playlist),
    ]

    log_path = session_dir / "ffmpeg.log"

    with open(log_path, "w") as log:
        log.write("COMMAND:\n")
        log.write(" ".join(cmd))
        log.write("\n\n")
        log.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    active = {
        "session_id": session_id,
        "channel": ch["guide_number"],
        "pid": proc.pid,
        "session_dir": str(session_dir),
        "playlist_url": f"/livebuffer/sessions/{session_id}/live.m3u8",
        "started_at": metadata["started_at"],
    }

    ACTIVE_FILE.write_text(json.dumps(active, indent=2))

    for _ in range(30):
        if playlist.exists() and playlist.stat().st_size > 0:
            break
        time.sleep(0.5)

    return active


def cleanup_old_sessions(days=3):
    cutoff = time.time() - (days * 86400)

    if not SESSIONS_DIR.exists():
        return 0

    removed = 0

    for session in SESSIONS_DIR.iterdir():
        if not session.is_dir():
            continue

        if session.stat().st_mtime < cutoff:
            shutil.rmtree(session, ignore_errors=True)
            removed += 1

    return removed