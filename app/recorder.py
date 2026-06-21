import datetime
import signal
import subprocess
from app import config
from app import database


class Recorder:
    def __init__(self):
        self.pidfile = config.BASE / "recording.pid"
        self.currentfile = config.BASE / "current_recording.txt"

    def is_recording(self):
        if not self.pidfile.exists():
            return False

        try:
            pid = int(self.pidfile.read_text().strip())
            subprocess.run(["kill", "-0", str(pid)], check=True)
            return True
        except Exception:
            self.pidfile.unlink(missing_ok=True)
            return False

    def start(self, channel="17.1", tuner="tuner1", rf_channel="auto:25", program="3"):
        if self.is_recording():
            return None

        start_time = datetime.datetime.now()
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")
        filename = f"{channel.replace('.', '_')}_{timestamp}.ts"

        outfile = config.RECORDINGS / filename
        logfile = config.LOGS / f"{filename}.log"

        database.add_recording(
            filename=filename,
            channel=channel,
            title=f"Manual Recording {channel}",
            start_time=start_time.isoformat(timespec="seconds")
        )

        subprocess.run(["hdhomerun_config", config.HDHR_DEVICE, "set", f"/{tuner}/channel", rf_channel])
        subprocess.run(["hdhomerun_config", config.HDHR_DEVICE, "set", f"/{tuner}/program", program])

        log = open(logfile, "w")

        proc = subprocess.Popen(
            ["hdhomerun_config", config.HDHR_DEVICE, "save", f"/{tuner}", str(outfile)],
            stdout=log,
            stderr=log,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        )

        self.pidfile.write_text(str(proc.pid))
        self.currentfile.write_text(filename)
        return filename

    def start_from_channel(self, channel_row):
        if self.is_recording():
            return None

        guide_number = channel_row["guide_number"]
        guide_name = channel_row["guide_name"] or guide_number
        url = channel_row["url"]

        start_time = datetime.datetime.now()
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")

        safe_name = guide_name.replace(" ", "_").replace("/", "_")
        filename = f"{guide_number.replace('.', '_')}_{safe_name}_{timestamp}.ts"

        outfile = config.RECORDINGS / filename
        logfile = config.LOGS / f"{filename}.log"

        database.add_recording(
            filename=filename,
            channel=guide_number,
            title=f"Manual Recording - {guide_name}",
            start_time=start_time.isoformat(timespec="seconds")
        )

        log = open(logfile, "w")

        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-i", url, "-c", "copy", str(outfile)],
            stdout=log,
            stderr=log
        )

        self.pidfile.write_text(str(proc.pid))
        self.currentfile.write_text(filename)
        return filename

    def stop(self, tuner="tuner1"):
        filename = None

        if self.currentfile.exists():
            filename = self.currentfile.read_text().strip()

        if self.pidfile.exists():
            try:
                pid = int(self.pidfile.read_text().strip())
                subprocess.run(["kill", "-INT", str(pid)])
            except Exception:
                pass

            self.pidfile.unlink(missing_ok=True)

        subprocess.run(["hdhomerun_config", config.HDHR_DEVICE, "set", f"/{tuner}/channel", "none"])

        if filename:
            path = config.RECORDINGS / filename
            size = path.stat().st_size if path.exists() else 0
            end_time = datetime.datetime.now().isoformat(timespec="seconds")
            database.finish_recording(filename, end_time, size)

        self.currentfile.unlink(missing_ok=True)
