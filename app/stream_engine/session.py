import datetime
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from app.stream_engine import index
from app import native_dvb

# Must match the "-hls_time" value passed to FFmpeg below.
HLS_SEGMENT_SECONDS = 2


class StreamSession:
    def __init__(
        self,
        session_id,
        channel,
        guide_name,
        source_url,
        session_dir,
        start_number=0,
        discontinuity_start=False,
        source_type="url",
        native_dvb_adapter=None,
    ):
        self.session_id = session_id
        self.channel = channel
        self.guide_name = guide_name
        self.source_url = source_url
        self.session_dir = Path(session_dir)

        self.source_type = str(
            source_type or "url"
        ).strip().lower()

        self.native_dvb_adapter = (
            None
            if native_dvb_adapter is None
            else int(native_dvb_adapter)
        )

        # Used by manager.py during seamless watchdog recovery.
        self.start_number = max(0, int(start_number or 0))
        self.discontinuity_start = bool(discontinuity_start)

        self.playlist = self.session_dir / "live.m3u8"
        self.segment_pattern = self.session_dir / "segment_%06d.ts"
        self.log_path = self.session_dir / "ffmpeg.log"
        self.metadata_path = self.session_dir / "metadata.json"
        self.pid_path = self.session_dir / "ffmpeg.pid"
        self.zap_pid_path = (
            self.session_dir
            / "dvbv5-zap.pid"
        )

    def cleanup_segments(self, keep_segments=900):
        segments = self.segment_files()

        if len(segments) <= keep_segments:
            return 0

        removed = 0

        for segment in segments[: len(segments) - keep_segments]:
            try:
                segment.unlink()
                removed += 1
            except OSError:
                pass

        return removed

    def write_metadata(self, program=None):
        self.session_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "session_id": self.session_id,
            "channel": self.channel,
            "guide_name": self.guide_name,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "native_dvb_adapter": self.native_dvb_adapter,
            "started_at": datetime.datetime.now().isoformat(),
            "playlist_url": self.playlist_url(),
            "program": program,
        }

        self.metadata_path.write_text(json.dumps(data, indent=2))
        return data

    def segment_files(self):
        return sorted(self.session_dir.glob("segment_*.ts"))

    def latest_segment_number(self):
        segments = self.segment_files()

        if not segments:
            return None

        try:
            return int(segments[-1].stem.rsplit("_", 1)[1])
        except (IndexError, TypeError, ValueError):
            return None

    def latest_segment_age(self):
        segments = self.segment_files()

        if not segments:
            return None

        try:
            return max(0.0, time.time() - segments[-1].stat().st_mtime)
        except OSError:
            return None

    def pid(self):
        if not self.pid_path.exists():
            return None

        try:
            return int(self.pid_path.read_text().strip())
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _process_state(pid):
        """Return the Linux process state letter, or None if unavailable."""
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text()
            closing_paren = stat_text.rfind(")")

            if closing_paren < 0:
                return None

            remaining = stat_text[closing_paren + 2 :].split()
            return remaining[0] if remaining else None
        except (FileNotFoundError, OSError, IndexError):
            return None

    def is_running(self):
        """Return True only when the PID points to a live, non-zombie process."""
        pid = self.pid()

        if not pid:
            return False

        state = self._process_state(pid)

        # Z = zombie/defunct; X/x = dead.
        if state in {"Z", "X", "x"}:
            self.pid_path.unlink(missing_ok=True)
            return False

        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            self.pid_path.unlink(missing_ok=True)
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def is_stalled(self, timeout_seconds=10):
        if not self.is_running():
            return False

        age = self.latest_segment_age()

        if age is None:
            return False

        threshold = max(
            float(timeout_seconds or 10),
            HLS_SEGMENT_SECONDS * 3,
        )

        return age > threshold

    def ready_status(self, min_segments=2):
        playlist_exists = (
            self.playlist.exists()
            and self.playlist.stat().st_size > 0
        )
        segments = self.segment_files()
        running = self.is_running()

        try:
            indexed_rows = (
                index.rebuild_index_from_files(self.session_dir) or []
            )
        except Exception:
            indexed_rows = []

        return {
            "ready": (
                running
                and playlist_exists
                and len(segments) >= int(min_segments or 1)
            ),
            "running": running,
            "playlist_exists": playlist_exists,
            "segment_count": len(segments),
            "index_count": len(indexed_rows),
            "latest_segment": segments[-1].name if segments else "",
            "latest_segment_age_seconds": self.latest_segment_age(),
            "min_segments": int(min_segments or 1),
        }

    def wait_until_ready(
        self,
        min_segments=2,
        timeout_seconds=10,
        poll_seconds=0.35,
    ):
        deadline = time.time() + float(timeout_seconds or 10)
        last_status = self.ready_status(min_segments=min_segments)

        while time.time() < deadline:
            last_status = self.ready_status(min_segments=min_segments)

            if last_status.get("ready"):
                return last_status

            time.sleep(float(poll_seconds or 0.35))

        return last_status

    def reset_output_files(self):
        self.session_dir.mkdir(parents=True, exist_ok=True)

        for path in self.session_dir.glob("segment_*.ts"):
            try:
                path.unlink()
            except OSError:
                pass

        for name in (
            "live.m3u8",
            "live.m3u8.tmp",
            "segments.json",
            "ffmpeg.pid",
            "dvbv5-zap.pid",
        ):
            try:
                (self.session_dir / name).unlink()
            except OSError:
                pass

    def start(self):
        """Start the FFmpeg HLS writer for this session."""
        self.session_dir.mkdir(parents=True, exist_ok=True)

        if self.is_running():
            return True

        self.pid_path.unlink(missing_ok=True)
        self.reset_output_files()

        hls_flags = [
            "program_date_time",
            "omit_endlist",
        ]

        if self.discontinuity_start:
            hls_flags.append("discont_start")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            # Without these, ffmpeg falls back to its default input probing
            # (~5s analyzeduration / 5MB probesize) before it will start
            # writing output. The live source is a known-format MPEG-TS
            # broadcast, so we don't need the full default window.
            #
            # 3s/3MB, not 1s/1MB: low-bitrate sub-channels (e.g. a ~1 Mbps
            # subchannel) only deliver ~120KB in 1 second, which isn't
            # always enough for ffmpeg to lock a precise frame-rate/PTS
            # estimate and can cause a timing-drift stall a minute or so
            # into playback. 3s gives those channels ~3x more data to work
            # with. Full-bitrate HD channels (15-19 Mbps) still hit the 3MB
            # probesize cap in well under a second either way, so this
            # doesn't cost them anything.
            "-analyzeduration",
            "3000000",
            "-probesize",
            "3000000",
            "-i",
            (
                "pipe:0"
                if self.source_type
                == "native_dvb"
                else self.source_url
            ),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "hls",
            "-hls_time",
            str(HLS_SEGMENT_SECONDS),
            "-hls_list_size",
            "900",
            "-start_number",
            str(self.start_number),
            "-hls_flags",
            "+".join(hls_flags),
            "-hls_segment_filename",
            str(self.segment_pattern),
            str(self.playlist),
        ]

        zap_process = None

        with open(self.log_path, "w") as log:
            if self.source_type == "native_dvb":
                service = native_dvb.get_service(
                    self.channel
                )

                if not service:
                    raise RuntimeError(
                        "Native DVB channel is not "
                        f"mapped: {self.channel}"
                    )

                self.native_dvb_adapter = (
                    native_dvb.acquire_adapter(
                        owner=self.session_id,
                        preferred=(
                            self.native_dvb_adapter
                        ),
                    )
                )

                zap_cmd = native_dvb.zap_command(
                    channel=self.channel,
                    adapter=self.native_dvb_adapter,
                    output="-",
                )

                log.write("DVB COMMAND:\n")
                log.write(" ".join(zap_cmd))
                log.write("\n\n")
                log.write("FFMPEG COMMAND:\n")
                log.write(" ".join(cmd))
                log.write("\n\n")
                log.flush()

                try:
                    zap_process = subprocess.Popen(
                        zap_cmd,
                        stdout=subprocess.PIPE,
                        stderr=log,
                        start_new_session=True,
                    )

                    self.zap_pid_path.write_text(
                        str(zap_process.pid)
                    )

                    process = subprocess.Popen(
                        cmd,
                        stdin=zap_process.stdout,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )

                    if zap_process.stdout:
                        zap_process.stdout.close()

                except Exception:
                    if (
                        zap_process is not None
                        and zap_process.poll() is None
                    ):
                        try:
                            os.killpg(
                                zap_process.pid,
                                signal.SIGKILL,
                            )
                        except Exception:
                            pass

                    self.zap_pid_path.unlink(
                        missing_ok=True
                    )

                    native_dvb.release_adapter(
                        self.native_dvb_adapter,
                        owner=self.session_id,
                    )
                    raise
            else:
                log.write("COMMAND:\n")
                log.write(" ".join(cmd))
                log.write("\n\n")
                log.flush()

                process = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

        self.pid_path.write_text(str(process.pid))

        # Require FFmpeg to create a real playlist and at least one segment.
        #
        # Native DVB can take a little longer on the first tune after the
        # frontend has been idle or initialized, so give that path 30 seconds
        # while preserving the existing 20-second network-tuner behavior.
        startup_checks = (
            60
            if self.source_type == "native_dvb"
            else 40
        )

        for _ in range(startup_checks):
            if process.poll() is not None:
                self.pid_path.unlink(missing_ok=True)

                if self.source_type == "native_dvb":
                    self._stop_native_dvb_input()

                return False

            if (
                self.playlist.exists()
                and self.playlist.stat().st_size > 0
                and self.segment_files()
            ):
                index.rebuild_index_from_files(self.session_dir)
                return True

            time.sleep(0.5)

        # Startup timed out. Do not leave FFmpeg, dvbv5-zap, or the physical
        # adapter reservation behind for the next tune attempt.
        try:
            if process.poll() is None:
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass

        self.pid_path.unlink(missing_ok=True)

        if self.source_type == "native_dvb":
            self._stop_native_dvb_input()

        return False

    def _stop_native_dvb_input(self):
        if self.source_type != "native_dvb":
            return

        zap_pid = None

        if self.zap_pid_path.exists():
            try:
                zap_pid = int(
                    self.zap_pid_path
                    .read_text()
                    .strip()
                )
            except Exception:
                zap_pid = None

        if zap_pid:
            try:
                os.killpg(
                    zap_pid,
                    signal.SIGKILL,
                )
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    os.kill(
                        zap_pid,
                        signal.SIGKILL,
                    )
                except Exception:
                    pass

        self.zap_pid_path.unlink(
            missing_ok=True
        )

        native_dvb.release_adapter(
            self.native_dvb_adapter,
            owner=self.session_id,
        )

    def stop(self):
        """Kill the FFmpeg writer for this session.

        This is used only for the disposable live-viewing / timeshift HLS
        remux (never for finalized recordings, which have their own
        separately-graceful stop path in recorder.py). There's no encoder
        state to flush here (-c:v copy) and reset_output_files() wipes any
        leftover segments on the next start(), so there's no benefit to
        waiting for a graceful exit -- go straight to SIGKILL to avoid
        stacking a multi-second wait on top of every channel change.
        """
        pid = self.pid()

        if not pid:
            self.pid_path.unlink(missing_ok=True)
            self._stop_native_dvb_input()
            return True

        if not self.is_running():
            self.pid_path.unlink(missing_ok=True)
            self._stop_native_dvb_input()
            return True

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            self.pid_path.unlink(missing_ok=True)
            return True
        except PermissionError:
            return False

        # SIGKILL is immediate; this just confirms the kernel has reaped it
        # before we let a new session reuse this directory.
        for _ in range(10):
            if not self.is_running():
                break

            time.sleep(0.05)

        self.pid_path.unlink(missing_ok=True)
        self._stop_native_dvb_input()
        return True

    def playlist_url(self):
        return (
            f"/livebuffer/sessions/"
            f"{self.session_id}/live.m3u8"
        )