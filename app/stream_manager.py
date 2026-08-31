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
 
        # h264_qsv depends on the oneVPL GPU runtime (vpl-gpu-rt), which is
        # inconsistently packaged on Ubuntu and only supports 11th-gen+
        # Intel CPUs. VA-API is the lower-level API QSV itself sits on top
        # of - using it directly avoids that whole dependency. Input decode
        # stays in software (proven working, and safely handles interlaced
        # OTA broadcast); only the expensive encode step goes to the GPU.
        "-init_hw_device", "vaapi=va:/dev/dri/renderD128",
        "-filter_hw_device", "va",
        # bwdif (motion-adaptive) instead of yadif - noticeably better
        # deinterlacing quality for a modest extra CPU cost on this
        # software filtering step, which is not the bottleneck here.
        "-vf", "bwdif=0:-1:0,format=nv12,hwupload",
 
        "-c:v", "h264_vaapi",
        # 1 = best quality/slowest on VAAPI's scale. Worth it: this step
        # runs on the GPU, so pushing quality all the way up costs
        # essentially nothing on CPU.
        "-quality", "1",
        "-bf", "3",
        "-profile:v", "high",
        # Confirmed from live_ffmpeg.log: this source's own MPEG-2 stream
        # is capped at a 19,400,000 bps VBV ceiling - that's the absolute
        # maximum the original broadcast encoder was ever allowed to use,
        # meaning there is no more picture information above that to
        # capture no matter how much H.264 bitrate we throw at it. Sitting
        # just under that is "as good as the broadcast can possibly be,"
        # not an arbitrary round number.
        "-b:v", "16000k",
        "-maxrate", "19000k",
        "-bufsize", "38000k",
 
        "-c:a", "aac",
        "-b:a", "192k",
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
 
    # Don't report success just because the ffmpeg process hasn't crashed
    # yet - Roku (and any other client) needs the playlist to actually
    # exist and reference at least one already-written segment, or it'll
    # fetch a missing/empty .m3u8 and fail with a generic "malformed HTTP
    # response" error. Poll for that instead of a blind 1s sleep.
    for _ in range(20):
        if proc.poll() is not None:
            # ffmpeg exited already - definitely not going to become ready.
            PIDFILE.unlink(missing_ok=True)
            return False
 
        if playlist.exists() and playlist.stat().st_size > 0:
            first_segment = next(config.LIVEBUFFER.glob("*.ts"), None)
 
            if first_segment is not None:
                return True
 
        time.sleep(0.5)
 
    return False
 
 
def status():
    return {
        "running": is_running(),
        "channel": current_channel(),
        "playlist_exists": (config.LIVEBUFFER / "live.m3u8").exists(),
        "log": str(LOGFILE),
    }