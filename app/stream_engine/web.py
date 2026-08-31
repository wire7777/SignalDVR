import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from app import config
from app import native_dvb


class WebStreamError(RuntimeError):
    pass


def _safe_component(value: str, fallback: str = "client") -> str:
    value = str(value or "").strip()

    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    ).strip("_")

    return safe[:80] or fallback


class WebStreamSession:
    """
    One browser-owned live stream.

    This session reads directly from the channel source URL and writes its own
    browser-compatible H.264/AAC HLS output. It does not call the global
    Android/Media3 live-session manager and therefore cannot retune or stop the
    Android app's active stream.
    """

    def __init__(
        self,
        client_id: str,
        channel: str,
        guide_name: str,
        source_url: str,
    ):
        self.client_id = _safe_component(client_id)
        self.channel = str(channel)
        self.guide_name = str(guide_name or channel)
        self.source_url = str(source_url)

        self.session_id = (
            f"{self.client_id}_"
            f"{_safe_component(self.channel, 'channel')}"
        )

        self.session_dir = (
            Path(config.LIVEBUFFER)
            / "web_sessions"
            / self.session_id
        )

        self.playlist = self.session_dir / "web.m3u8"
        self.segment_pattern = self.session_dir / "web_%06d.ts"
        self.pid_path = self.session_dir / "web_ffmpeg.pid"
        self.log_path = self.session_dir / "web_ffmpeg.log"
        self.metadata_path = self.session_dir / "metadata.json"
        self.last_seen = time.time()

        # Native DVB browser playback owns its own physical adapter
        # and dvbv5-zap process. HDHomeRun leaves these unused.
        self.native_dvb_adapter = None
        self.native_dvb_owner = ""
        self.native_dvb_process = None
        self.ffmpeg_process = None

    def touch(self) -> None:
        self.last_seen = time.time()

    def playlist_url(self) -> str:
        return (
            f"/livebuffer/web_sessions/"
            f"{self.session_id}/web.m3u8"
        )

    def pid(self) -> Optional[int]:
        if not self.pid_path.exists():
            return None

        try:
            return int(self.pid_path.read_text().strip())
        except (OSError, TypeError, ValueError):
            return None

    def is_running(self) -> bool:
        pid = self.pid()

        if not pid:
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

    def _write_metadata(self) -> None:
        payload = {
            "client_id": self.client_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "guide_name": self.guide_name,
            "source_url": self.source_url,
            "playlist_url": self.playlist_url(),
            "started_at": time.time(),
        }

        self.metadata_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def _clear_output(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)

        for path in self.session_dir.glob("web_*.ts"):
            try:
                path.unlink()
            except OSError:
                pass

        for name in (
            "web.m3u8",
            "web.m3u8.tmp",
            "web_ffmpeg.pid",
        ):
            try:
                (self.session_dir / name).unlink()
            except OSError:
                pass

    def start(self) -> bool:
        self.session_dir.mkdir(parents=True, exist_ok=True)

        if (
            self.is_running()
            and self.playlist.exists()
            and self.playlist.stat().st_size > 0
        ):
            return True

        self.stop()
        self._clear_output()
        self._write_metadata()

        source_type = (
            "native_dvb"
            if self.source_url.startswith("native-dvb://")
            else "hdhomerun"
        )

        ffmpeg_input = []

        try:
            if source_type == "native_dvb":
                service = native_dvb.get_service(
                    self.channel
                )

                if not service:
                    raise WebStreamError(
                        f"Native DVB channel not mapped: "
                        f"{self.channel}"
                    )

                self.native_dvb_owner = (
                    f"web-{self.session_id}"
                )

                self.native_dvb_adapter = (
                    native_dvb.acquire_adapter(
                        owner=self.native_dvb_owner
                    )
                )

                zap_command = native_dvb.zap_command(
                    self.channel,
                    adapter=self.native_dvb_adapter,
                    output="-",
                )

                with self.log_path.open(
                    "w",
                    encoding="utf-8",
                ) as log:
                    log.write(
                        "NATIVE DVB COMMAND:\n"
                    )
                    log.write(
                        " ".join(zap_command)
                    )
                    log.write("\n\n")
                    log.flush()

                    self.native_dvb_process = (
                        subprocess.Popen(
                            zap_command,
                            stdout=subprocess.PIPE,
                            stderr=log,
                            start_new_session=True,
                        )
                    )

                if self.native_dvb_process.stdout is None:
                    raise WebStreamError(
                        "Native DVB process has no stdout"
                    )

                ffmpeg_input = [
                    "-analyzeduration",
                    "3000000",
                    "-probesize",
                    "3000000",
                    "-i",
                    "pipe:0",
                ]

            else:
                ffmpeg_input = [
                    "-i",
                    self.source_url,
                ]

            command = [
                "ffmpeg",
                "-nostdin",
                "-y",

                *ffmpeg_input,

                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",

                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",

                "-g",
                "60",
                "-keyint_min",
                "60",
                "-sc_threshold",
                "0",

                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ac",
                "2",
                "-ar",
                "48000",

                "-f",
                "hls",
                "-hls_time",
                "2",
                "-hls_list_size",
                "900",
                "-hls_flags",
                (
                    "delete_segments+append_list+"
                    "program_date_time+omit_endlist"
                ),
                "-hls_segment_filename",
                str(self.segment_pattern),
                str(self.playlist),
            ]

            log_mode = (
                "a"
                if source_type == "native_dvb"
                else "w"
            )

            with self.log_path.open(
                log_mode,
                encoding="utf-8",
            ) as log:
                log.write("FFMPEG COMMAND:\n")
                log.write(" ".join(command))
                log.write("\n\n")
                log.flush()

                process = subprocess.Popen(
                    command,
                    stdin=(
                        self.native_dvb_process.stdout
                        if self.native_dvb_process
                        else None
                    ),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

                self.ffmpeg_process = process

            if (
                self.native_dvb_process is not None
                and self.native_dvb_process.stdout is not None
            ):
                self.native_dvb_process.stdout.close()

            self.pid_path.write_text(
                str(process.pid)
            )

            # Native DVB can need longer for the initial ATSC lock.
            deadline = time.time() + (
                30.0
                if source_type == "native_dvb"
                else 20.0
            )

            while time.time() < deadline:
                if process.poll() is not None:
                    self.pid_path.unlink(
                        missing_ok=True
                    )

                    self.stop()

                    return False

                if (
                    self.native_dvb_process is not None
                    and self.native_dvb_process.poll()
                    is not None
                ):
                    self.stop()
                    return False

                if (
                    self.playlist.exists()
                    and self.playlist.stat().st_size > 0
                    and len(
                        list(
                            self.session_dir.glob(
                                "web_*.ts"
                            )
                        )
                    ) >= 2
                ):
                    return True

                time.sleep(0.35)

            # Startup timed out. Do not leave either FFmpeg,
            # dvbv5-zap, or the adapter reservation behind.
            self.stop()
            return False

        except Exception:
            self.stop()
            raise

    def stop(self) -> bool:
        success = True

        # -------------------------------------------------
        # Stop browser FFmpeg
        # -------------------------------------------------

        pid = self.pid()

        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                success = False

            deadline = time.time() + 5.0

            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.2)
                except ProcessLookupError:
                    break
                except PermissionError:
                    break
                except OSError:
                    break
            else:
                try:
                    os.killpg(
                        pid,
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass
                except PermissionError:
                    success = False

        self.pid_path.unlink(
            missing_ok=True
        )

        # Reap the browser FFmpeg child. Killing a child process without
        # wait() leaves a zombie entry such as "[ffmpeg] <defunct>".
        if self.ffmpeg_process is not None:
            try:
                self.ffmpeg_process.wait(
                    timeout=2.0
                )
            except subprocess.TimeoutExpired:
                try:
                    self.ffmpeg_process.kill()
                except Exception:
                    pass

                try:
                    self.ffmpeg_process.wait(
                        timeout=2.0
                    )
                except Exception:
                    pass
            except Exception:
                pass

            self.ffmpeg_process = None

        # -------------------------------------------------
        # Stop Native DVB dvbv5-zap
        # -------------------------------------------------

        if self.native_dvb_process is not None:
            native_pid = (
                self.native_dvb_process.pid
            )

            try:
                os.killpg(
                    native_pid,
                    signal.SIGTERM,
                )
            except ProcessLookupError:
                pass
            except PermissionError:
                success = False

            try:
                self.native_dvb_process.wait(
                    timeout=3.0
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(
                        native_pid,
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass
                except PermissionError:
                    success = False

                try:
                    self.native_dvb_process.wait(
                        timeout=2.0
                    )
                except Exception:
                    pass

            self.native_dvb_process = None

        # -------------------------------------------------
        # Release physical DVB adapter
        # -------------------------------------------------

        if self.native_dvb_adapter is not None:
            adapter = self.native_dvb_adapter

            try:
                native_dvb.release_adapter(
                    adapter,
                    owner=self.native_dvb_owner,
                )

                print(
                    "Native DVB web adapter released:",
                    adapter,
                    flush=True,
                )

            except Exception as exc:
                print(
                    "Native DVB web adapter release error:",
                    exc,
                    flush=True,
                )

                success = False

            self.native_dvb_adapter = None
            self.native_dvb_owner = ""

        return success


class WebStreamManager:
    """
    Keeps one independent web stream per browser client.

    Different devices can tune different channels without touching the global
    Android live session. A browser changing channels stops only its own prior
    web FFmpeg process.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, WebStreamSession] = {}

        # Browser sessions are short-lived tuner consumers. The normal
        # SignalDVR cleanup service runs every 15 minutes, which is too slow
        # for releasing a tuner after someone closes a browser tab.
        #
        # Run a very lightweight web-session cleanup check independently.
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="signaldvr-web-stream-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def _cleanup_loop(self):
        """Release abandoned browser streams promptly.

        Browser pages send heartbeats while active. If no heartbeat/touch
        occurs for 90 seconds, cleanup_stale() stops FFmpeg, stops dvbv5-zap,
        and releases any Native DVB adapter owned by that browser session.
        """
        while True:
            try:
                removed = self.cleanup_stale(
                    max_idle_seconds=90.0
                )

                if removed:
                    print(
                        "Web stream cleanup:",
                        f"removed={removed}",
                        flush=True,
                    )

            except Exception as error:
                print(
                    "Web stream cleanup error:",
                    error,
                    flush=True,
                )

            time.sleep(15.0)


    @staticmethod
    def client_id(remote_addr: str, user_agent: str) -> str:
        raw = f"{remote_addr or 'unknown'}|{user_agent or 'unknown'}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"web_{digest}"

    def start(
        self,
        client_id: str,
        channel: str,
        guide_name: str,
        source_url: str,
    ) -> WebStreamSession:
        with self._lock:
            existing = self._sessions.get(client_id)

            if (
                existing is not None
                and existing.channel == str(channel)
                and existing.source_url == str(source_url)
                and existing.is_running()
            ):
                existing.touch()
                return existing

            if existing is not None:
                existing.stop()

            session = WebStreamSession(
                client_id=client_id,
                channel=channel,
                guide_name=guide_name,
                source_url=source_url,
            )

            if not session.start():
                raise WebStreamError(
                    "Timed out waiting for browser HLS stream. "
                    f"Check {session.log_path}"
                )

            session.touch()
            self._sessions[client_id] = session
            return session

    def stop(self, client_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(client_id, None)

        if session is None:
            return True

        return session.stop()

    def touch(self, client_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(client_id)
            if session is None:
                return False
            session.touch()
            return True

    def cleanup_stale(self, max_idle_seconds: float = 90.0) -> int:
        cutoff = time.time() - max(30.0, float(max_idle_seconds))
        stale = []

        with self._lock:
            for client_id, session in list(self._sessions.items()):
                if session.last_seen < cutoff or not session.is_running():
                    stale.append((client_id, session))
                    self._sessions.pop(client_id, None)

        for _, session in stale:
            session.stop()

        return len(stale)

    def status(self, client_id: str) -> Optional[dict]:
        with self._lock:
            session = self._sessions.get(client_id)

            if session is None:
                return None

            return {
                "client_id": session.client_id,
                "session_id": session.session_id,
                "channel": session.channel,
                "guide_name": session.guide_name,
                "playlist_url": session.playlist_url(),
                "running": session.is_running(),
                "last_seen": session.last_seen,
            }


web_stream_manager = WebStreamManager()