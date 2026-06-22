import subprocess
import time

from app import config
from app import database


PIDFILE = config.BASE / "stream_manager.pid"
CHANNEL_FILE = config.BASE / "stream_channel.txt"
LOGFILE = config.LOGS / "live_ffmpeg.log"


def _pid_running(pid):
    try:
        subprocess.run(["kill", "-0", str(pid)], check=True)
        return True
    except Exception:
        return False


def is_running():
    if not PIDFILE.exists():
        return False

    try:
        pid = int(PIDFILE.read_text().strip())

        if _pid_running(pid):
            return True

        PIDFILE.unlink(missing_ok=True)
        return False

    except Exception:
        PIDFILE.unlink(missing_ok=True)
        return False


def stop_stream():
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text().strip())

            subprocess.run(["kill", "-TERM", str(pid)])
            time.sleep(1)

            if _pid_running(pid):
                subprocess.run(["kill", "-KILL", str(pid)])

        except Exception:
            pass

        PIDFILE.unlink(missing_ok=True)


def clean_buffer():
    config.LIVEBUFFER.mkdir(parents=True, exist_ok=True)

    for f in config.LIVEBUFFER.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass


def current_channel():
    if CHANNEL_FILE.exists():
        return CHANNEL_FILE.read_text().strip()

    return ""


def start_stream(channel_number):
    ch = database.get_channel(channel_number)

    if not ch:
        return False

    stop_stream()
    clean_buffer()

    config.LOGS.mkdir(parents=True, exist_ok=True)

    playlist = config.LIVEBUFFER / "live.m3u8"

    CHANNEL_FILE.write_text(
        f"{ch['guide_number']} {ch['guide_name']}"
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-i", ch["url"],

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",

        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",

        "-g", "60",
        "-sc_threshold", "0",

        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "1800",
        "-hls_flags", "append_list+program_date_time",

        str(playlist),
    ]

    with open(LOGFILE, "w") as log:
        log.write("COMMAND:\n")
        log.write(" ".join(cmd))
        log.write("\n\n")
        log.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    PIDFILE.write_text(str(proc.pid))

    time.sleep(1)

    if not is_running():
        return False

    return True


def status():
    return {
        "running": is_running(),
        "channel": current_channel(),
        "playlist_exists": (config.LIVEBUFFER / "live.m3u8").exists(),
        "log": str(LOGFILE),
    }