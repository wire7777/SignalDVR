import datetime
import os
import signal
import subprocess
import time

from app import config
from app import database
from app import recording_finalize
from app import native_dvb


class Recorder:
    def __init__(self, state_key="manual"):
        # Scheduler creates one Recorder per scheduled recording.
        # Give every instance independent PID/current-file state so
        # simultaneous recordings do not block each other.
        safe_key = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(state_key or "manual")
        )

        state_dir = config.BASE / "recording_state"
        state_dir.mkdir(parents=True, exist_ok=True)

        self.state_key = safe_key
        self.pidfile = state_dir / f"{safe_key}.pid"
        self.currentfile = state_dir / f"{safe_key}.txt"

        self.source_type = ""
        self.native_dvb_adapter = None
        self.native_dvb_owner = ""
        self.native_dvb_process = None
        self.log_handle = None

    def is_recording(self):
        if not self.pidfile.exists():
            return False

        try:
            pid = int(self.pidfile.read_text().strip())
            os.kill(pid, 0)
            return True
        except Exception:
            self.pidfile.unlink(missing_ok=True)
            self.currentfile.unlink(missing_ok=True)
            return False

    def start(
        self,
        channel="17.1",
        tuner="tuner1",
        rf_channel="auto:25",
        program="3",
        title=None,
    ):
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
            title=title or f"Manual Recording {channel}",
            start_time=start_time.isoformat(timespec="seconds"),
            status="Recording",
        )

        subprocess.run(
            [
                "hdhomerun_config",
                config.HDHR_DEVICE,
                "set",
                f"/{tuner}/channel",
                rf_channel,
            ],
            check=False,
        )
        subprocess.run(
            [
                "hdhomerun_config",
                config.HDHR_DEVICE,
                "set",
                f"/{tuner}/program",
                program,
            ],
            check=False,
        )

        log = open(logfile, "w")

        proc = subprocess.Popen(
            [
                "hdhomerun_config",
                config.HDHR_DEVICE,
                "save",
                f"/{tuner}",
                str(outfile),
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

        self.pidfile.write_text(str(proc.pid))
        self.currentfile.write_text(filename)

        return filename

    def start_from_channel(self, channel_row, title=None):
        if self.is_recording():
            return None

        guide_number = channel_row["guide_number"]
        guide_name = channel_row["guide_name"] or guide_number
        url = channel_row["url"]

        start_time = datetime.datetime.now()
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")

        safe_name = (
            (title or guide_name)
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
            .replace("?", "")
            .replace('"', "")
            .replace("'", "")
        )

        filename = f"{guide_number.replace('.', '_')}_{safe_name}_{timestamp}.ts"

        outfile = config.RECORDINGS / filename
        logfile = config.LOGS / f"{filename}.log"

        database.add_recording(
            filename=filename,
            channel=guide_number,
            title=title or f"Manual Recording - {guide_name}",
            start_time=start_time.isoformat(timespec="seconds"),
            status="Recording",
        )

        log = open(logfile, "w")

        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-i",
                url,
                "-c",
                "copy",
                str(outfile),
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

        self.pidfile.write_text(str(proc.pid))
        self.currentfile.write_text(filename)

        return filename

    def start_from_schedule(self, channel_row, schedule):
        if self.is_recording():
            return None

        guide_number = str(
            channel_row["guide_number"]
        )

        guide_name = (
            channel_row["guide_name"]
            or guide_number
        )

        url = str(
            channel_row.get("url")
            or ""
        ).strip()

        self.source_type = str(
            channel_row.get("_tuner_source")
            or ""
        ).strip().lower()

        title = (
            schedule.get("title", "")
            or f"Manual Recording - {guide_name}"
        )

        start_time = datetime.datetime.now()
        timestamp = start_time.strftime(
            "%Y%m%d_%H%M%S"
        )

        safe_name = (
            title
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
            .replace("?", "")
            .replace('"', "")
            .replace("'", "")
        )

        filename = (
            f"{guide_number.replace('.', '_')}_"
            f"{safe_name}_{timestamp}.ts"
        )

        outfile = config.RECORDINGS / filename
        logfile = config.LOGS / f"{filename}.log"

        database.add_recording_from_schedule(
            filename=filename,
            schedule=schedule,
            start_time=start_time.isoformat(
                timespec="seconds"
            ),
            status="Recording",
        )

        self.log_handle = open(
            logfile,
            "w",
        )

        try:
            # --------------------------------------------------
            # Native Linux DVB
            # --------------------------------------------------
            if self.source_type == "native_dvb":
                service = native_dvb.get_service(
                    guide_number
                )

                if not service:
                    raise RuntimeError(
                        "Native DVB channel not mapped: "
                        f"{guide_number}"
                    )

                schedule_id = schedule.get(
                    "id",
                    self.state_key,
                )

                self.native_dvb_owner = (
                    f"recording-{schedule_id}"
                )

                self.native_dvb_adapter = (
                    native_dvb.acquire_adapter(
                        owner=self.native_dvb_owner
                    )
                )

                zap_cmd = native_dvb.zap_command(
                    channel=guide_number,
                    adapter=self.native_dvb_adapter,
                    output="-",
                )

                print(
                    "Native DVB recording starting:",
                    f"schedule={schedule_id}",
                    f"channel={guide_number}",
                    f"service={service.get('name', guide_name)}",
                    f"adapter={self.native_dvb_adapter}",
                    flush=True,
                )

                self.log_handle.write(
                    "DVB COMMAND:\n"
                )
                self.log_handle.write(
                    " ".join(zap_cmd)
                )
                self.log_handle.write(
                    "\n\nFFMPEG COMMAND:\n"
                )

                self.native_dvb_process = (
                    subprocess.Popen(
                        zap_cmd,
                        stdout=subprocess.PIPE,
                        stderr=self.log_handle,
                        start_new_session=True,
                    )
                )

                ffmpeg_cmd = [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-analyzeduration",
                    "3000000",
                    "-probesize",
                    "3000000",
                    "-i",
                    "pipe:0",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c",
                    "copy",
                    str(outfile),
                ]

                self.log_handle.write(
                    " ".join(ffmpeg_cmd)
                )
                self.log_handle.write("\n\n")
                self.log_handle.flush()

                proc = subprocess.Popen(
                    ffmpeg_cmd,
                    stdin=self.native_dvb_process.stdout,
                    stdout=self.log_handle,
                    stderr=self.log_handle,
                    start_new_session=True,
                )

                if self.native_dvb_process.stdout:
                    self.native_dvb_process.stdout.close()

            # --------------------------------------------------
            # Existing tuner URL path
            # --------------------------------------------------
            else:
                if not url:
                    raise RuntimeError(
                        "Channel has no recording source URL"
                    )

                proc = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-i",
                        url,
                        "-c",
                        "copy",
                        str(outfile),
                    ],
                    stdout=self.log_handle,
                    stderr=self.log_handle,
                    start_new_session=True,
                )

            self.pidfile.write_text(
                str(proc.pid)
            )

            self.currentfile.write_text(
                filename
            )

            # FFmpeg should remain alive while the tuner locks.
            time.sleep(1.0)

            if not self._process_is_running(
                proc.pid
            ):
                raise RuntimeError(
                    "Recording FFmpeg exited during startup"
                )

            return filename

        except Exception:
            # Kill DVB producer if startup failed.
            if self.native_dvb_process is not None:
                try:
                    pid = self.native_dvb_process.pid

                    if self._process_is_running(pid):
                        try:
                            os.killpg(
                                pid,
                                signal.SIGKILL,
                            )
                        except Exception:
                            try:
                                os.kill(
                                    pid,
                                    signal.SIGKILL,
                                )
                            except Exception:
                                pass
                except Exception:
                    pass

                self.native_dvb_process = None

            # Release physical tuner reservation.
            if self.native_dvb_adapter is not None:
                try:
                    native_dvb.release_adapter(
                        self.native_dvb_adapter,
                        owner=self.native_dvb_owner,
                    )
                except Exception:
                    pass

                self.native_dvb_adapter = None
                self.native_dvb_owner = ""

            self.pidfile.unlink(
                missing_ok=True
            )
            self.currentfile.unlink(
                missing_ok=True
            )

            if self.log_handle is not None:
                try:
                    self.log_handle.close()
                except Exception:
                    pass

                self.log_handle = None

            # Do not leave a phantom Recording row.
            try:
                database.delete_recording(
                    filename
                )
            except Exception:
                pass

            raise

    @staticmethod
    def _process_is_running(pid):
        """
        Return True only when the process is genuinely running.

        Linux keeps exited child processes in zombie state until their parent
        reaps them. A zombie still responds to kill(pid, 0), so check
        /proc/<pid>/stat before treating the PID as active.
        """
        stat_path = f"/proc/{pid}/stat"

        try:
            with open(stat_path, "r", encoding="utf-8") as stat_file:
                fields = stat_file.read().split()

            # Field 3 is the Linux process state.
            # Z = zombie, X/x = dead.
            if len(fields) >= 3 and fields[2] in {"Z", "X", "x"}:
                return False

            os.kill(pid, 0)
            return True

        except FileNotFoundError:
            return False
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False

    @staticmethod
    def _wait_for_exit(pid, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            if not Recorder._process_is_running(pid):
                return True
            time.sleep(0.2)

        return not Recorder._process_is_running(pid)

    def _stop_process(self, pid):
        """
        Stop the recorder process and do not return until it is gone.

        The recording processes are started in their own session, so signals
        are sent to the entire process group. This also catches any child
        process created by FFmpeg or hdhomerun_config.
        """
        if not self._process_is_running(pid):
            return

        print("Stopping recorder process:", pid, flush=True)

        for sig, wait_seconds, label in (
            (signal.SIGINT, 8, "SIGINT"),
            (signal.SIGTERM, 5, "SIGTERM"),
            (signal.SIGKILL, 3, "SIGKILL"),
        ):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                return
            except Exception as exc:
                print(
                    f"Unable to send {label} to recorder process group {pid}:",
                    exc,
                    flush=True,
                )
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    return

            if self._wait_for_exit(pid, wait_seconds):
                print(
                    f"Recorder process {pid} stopped after {label}",
                    flush=True,
                )
                return

        raise RuntimeError(f"Recorder process {pid} did not stop")

    def stop(self, tuner="tuner1"):
        filename = None
        recording = None
        rule_id = 0
        process_error = None

        if self.currentfile.exists():
            try:
                filename = self.currentfile.read_text().strip() or None
            except Exception as exc:
                print("Unable to read current recording filename:", exc, flush=True)

        try:
            if self.pidfile.exists():
                try:
                    pid = int(self.pidfile.read_text().strip())
                    self._stop_process(pid)
                except Exception as exc:
                    process_error = exc
                    print("Recorder process stop error:", exc, flush=True)

            # Only the legacy HDHomeRun recorder path needs to explicitly
            # release an HDHomeRun hardware tuner. Native DVB owns/release its
            # /dev/dvb adapter separately below.
            if self.source_type != "native_dvb":
                subprocess.run(
                    [
                        "hdhomerun_config",
                        config.HDHR_DEVICE,
                        "set",
                        f"/{tuner}/channel",
                        "none",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

            # Never mark the recording complete while its writer is still alive.
            if process_error is not None:
                raise process_error

            if filename:
                path = config.RECORDINGS / filename
                size = path.stat().st_size if path.exists() else 0
                end_time = datetime.datetime.now().isoformat(timespec="seconds")

                database.finish_recording(filename, end_time, size)
                recording = database.get_recording(filename)

                print(
                    "Recording finalized:",
                    filename,
                    "size",
                    size,
                    flush=True,
                )

                try:
                    recording_finalize.finalize_recording(filename)
                except Exception as exc:
                    # The TS file and recording DB row are already complete.
                    # A post-processing failure must not leave it marked active.
                    print(
                        "Recording post-finalize error:",
                        filename,
                        exc,
                        flush=True,
                    )

            if recording:
                rule_id = int(recording.get("recording_rule") or 0)

            if rule_id:
                rule = database.get_series_recording(rule_id)

                if rule:
                    keep_last = int(rule.get("keep_last") or 0)
                    old_recordings = database.get_recordings_to_prune_for_rule(
                        rule_id,
                        keep_last,
                    )

                    for old in old_recordings:
                        old_file = old.get("filename")
                        old_path = config.RECORDINGS / old_file

                        print("Pruning old recording:", old_file, flush=True)

                        try:
                            if old_path.exists():
                                old_path.unlink()
                        except Exception as exc:
                            print(
                                "Prune file delete error:",
                                old_file,
                                exc,
                                flush=True,
                            )

                        try:
                            database.delete_recording(old_file)
                        except Exception as exc:
                            print(
                                "Prune database delete error:",
                                old_file,
                                exc,
                                flush=True,
                            )
        finally:
            # Native DVB has a separate dvbv5-zap process feeding
            # the recording FFmpeg process. Stop that producer and release
            # the physical adapter after FFmpeg has closed the recording.
            if self.native_dvb_process is not None:
                try:
                    native_pid = self.native_dvb_process.pid

                    if self._process_is_running(
                        native_pid
                    ):
                        try:
                            os.killpg(
                                native_pid,
                                signal.SIGINT,
                            )
                        except Exception:
                            try:
                                os.kill(
                                    native_pid,
                                    signal.SIGINT,
                                )
                            except Exception:
                                pass

                        if not self._wait_for_exit(
                            native_pid,
                            3,
                        ):
                            try:
                                os.killpg(
                                    native_pid,
                                    signal.SIGKILL,
                                )
                            except Exception:
                                try:
                                    os.kill(
                                        native_pid,
                                        signal.SIGKILL,
                                    )
                                except Exception:
                                    pass

                except Exception as exc:
                    print(
                        "Native DVB recording process cleanup error:",
                        exc,
                        flush=True,
                    )

                self.native_dvb_process = None

            if self.native_dvb_adapter is not None:
                adapter = self.native_dvb_adapter

                try:
                    native_dvb.release_adapter(
                        adapter,
                        owner=self.native_dvb_owner,
                    )

                    print(
                        "Native DVB recording adapter released:",
                        adapter,
                        flush=True,
                    )

                except Exception as exc:
                    print(
                        "Native DVB adapter release error:",
                        exc,
                        flush=True,
                    )

                self.native_dvb_adapter = None
                self.native_dvb_owner = ""

            if self.log_handle is not None:
                try:
                    self.log_handle.close()
                except Exception:
                    pass

                self.log_handle = None

            # Clear state only after the stop/finalize attempt has completed.
            self.pidfile.unlink(missing_ok=True)
            self.currentfile.unlink(missing_ok=True)