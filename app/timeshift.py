import subprocess
import time
from datetime import datetime

from app import config
from app import database

POSITION_FILE = config.BASE / "timeshift_position.txt"
PAUSED_FILE = config.BASE / "timeshift_paused.txt"

PIDFILE_RAW = config.BASE / "timeshift_raw.pid"
PIDFILE_HLS = config.BASE / "timeshift_hls.pid"

CHANNEL_FILE = config.BASE / "timeshift_channel.txt"
CURRENT_FILE = config.BASE / "timeshift_current.txt"

RAW_LOG = config.LOGS / "timeshift_raw.log"
HLS_LOG = config.LOGS / "timeshift_hls.log"


def _pid_running(pid):
    try:
        subprocess.run(["kill", "-0", str(pid)], check=True)
        return True
    except Exception:
        return False


def _stop_pid(pidfile):
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            subprocess.run(["kill", "-TERM", str(pid)])
            time.sleep(1)

            if _pid_running(pid):
                subprocess.run(["kill", "-KILL", str(pid)])
        except Exception:
            pass

        pidfile.unlink(missing_ok=True)


def stop():
    _stop_pid(PIDFILE_HLS)
    _stop_pid(PIDFILE_RAW)


def current_file():
    if CURRENT_FILE.exists():
        return CURRENT_FILE.read_text().strip()
    return ""


def current_channel():
    if CHANNEL_FILE.exists():
        return CHANNEL_FILE.read_text().strip()
    return ""


def raw_running():
    if not PIDFILE_RAW.exists():
        return False

    try:
        pid = int(PIDFILE_RAW.read_text().strip())
        return _pid_running(pid)
    except Exception:
        return False


def hls_running():
    if not PIDFILE_HLS.exists():
        return False

    try:
        pid = int(PIDFILE_HLS.read_text().strip())
        return _pid_running(pid)
    except Exception:
        return False


def clean_hls():
    config.LIVEBUFFER.mkdir(parents=True, exist_ok=True)

    for f in config.LIVEBUFFER.glob("timeshift_hls*"):
        try:
            f.unlink()
        except Exception:
            pass


def start(channel_number):
    ch = database.get_channel(channel_number)

    if not ch:
        return False

    stop()

    config.LIVEBUFFER.mkdir(parents=True, exist_ok=True)
    config.LOGS.mkdir(parents=True, exist_ok=True)

    clean_hls()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_channel = channel_number.replace(".", "_")
    filename = f"timeshift_{safe_channel}_{stamp}.ts"
    raw_path = config.LIVEBUFFER / filename

    CHANNEL_FILE.write_text(f"{ch['guide_number']} {ch['guide_name']}")
    CURRENT_FILE.write_text(filename)

    raw_cmd = [
        "ffmpeg",
        "-y",
        "-i", ch["url"],
        "-c", "copy",
        str(raw_path),
    ]

    with open(RAW_LOG, "w") as log:
        log.write("RAW COMMAND:\n")
        log.write(" ".join(raw_cmd))
        log.write("\n\n")
        log.flush()

        raw_proc = subprocess.Popen(
            raw_cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    PIDFILE_RAW.write_text(str(raw_proc.pid))

    # Give the raw DVR buffer time to exist and receive data.
    for _ in range(20):
        if raw_path.exists() and raw_path.stat().st_size > 2_000_000:
            break
        time.sleep(0.5)

    hls_playlist = config.LIVEBUFFER / "timeshift_hls.m3u8"

    hls_cmd = [
        "ffmpeg",
        "-y",
        "-re",
        "-i", str(raw_path),

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

        str(hls_playlist),
    ]

    with open(HLS_LOG, "w") as log:
        log.write("HLS COMMAND:\n")
        log.write(" ".join(hls_cmd))
        log.write("\n\n")
        log.flush()

        hls_proc = subprocess.Popen(
            hls_cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    PIDFILE_HLS.write_text(str(hls_proc.pid))

    return True


def status():
    filename = current_file()
    raw_path = config.LIVEBUFFER / filename if filename else None
    hls_path = config.LIVEBUFFER / "timeshift_hls.m3u8"
    

    return {
    "channel": current_channel(),
    "file": filename,
    "raw_exists": raw_path.exists() if raw_path else False,
    "raw_size": raw_path.stat().st_size if raw_path and raw_path.exists() else 0,
    "hls_exists": hls_path.exists(),
    "raw_running": raw_running(),
    "hls_running": hls_running(),
    "position": get_position(),
    "paused": is_paused(),
    "raw_log": str(RAW_LOG),
    "hls_log": str(HLS_LOG),
}

def set_position(seconds):
    POSITION_FILE.write_text(str(int(seconds)))
    return True


def get_position():
    if POSITION_FILE.exists():
        try:
            return int(POSITION_FILE.read_text().strip())
        except Exception:
            return 0
    return 0


def pause():
    PAUSED_FILE.write_text("1")
    return True


def resume():
    PAUSED_FILE.unlink(missing_ok=True)
    return True


def is_paused():
    return PAUSED_FILE.exists()


def seek_relative(seconds):
    current = get_position()
    new_pos = max(0, current + int(seconds))
    set_position(new_pos)
    return new_pos


def jump_to_live():
    set_position(0)
    resume()
    return True