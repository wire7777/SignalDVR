import datetime
import threading
import time
import json
import os
import shutil
import subprocess
from pathlib import Path

from app import config, database
from app import live_segments
from app.stream_engine.session import StreamSession, HLS_SEGMENT_SECONDS
from app.stream_engine import buffer
from app.stream_engine import index
from app.stream_engine import metadata


SESSIONS_DIR = config.LIVEBUFFER / "sessions"
ACTIVE_FILE = config.LIVEBUFFER / "active_live_session.json"

# Live TV rewind-buffer setting.
#
# The Settings page stores this value as a segment count. SignalDVR currently
# creates 2-second HLS segments:
#
# 1800 segments  = 1 hour
# 5400 segments  = 3 hours
# 10800 segments = 6 hours
# 21600 segments = 12 hours
# 43200 segments = 24 hours
#
# This setting controls only the growing live-TV rewind window. It does not
# control when completed Background DVR programs are deleted.
DEFAULT_KEEP_SEGMENTS = 1800

# Background DVR retention is intentionally separate from the live-TV rewind
# buffer. It applies only to completed, unsaved Background DVR programs.
#
# Saved programs must never be automatically deleted.
DEFAULT_BACKGROUND_DVR_RETENTION = "end_of_day"

BACKGROUND_DVR_RETENTION_OPTIONS = {
    "end_of_day",
    "1_day",
    "3_days",
    "7_days",
    "14_days",
    "never",
}

# Self-healing live watchdog.
WATCHDOG_INTERVAL = 5
STALL_TIMEOUT = 15
WATCHDOG_RECOVERY_COOLDOWN = 60

# Serialize all live-session stop/start operations. An RLock is required
# because start_or_reuse() may call stop_active() while already holding it.
_live_session_lock = threading.RLock()

_watchdog_started = False
_watchdog_thread = None
_watchdog_recovering = False
_watchdog_last_recovery = 0.0

_watchdog_session_id = None
_last_segment = None
_last_segment_mtime = 0.0
_last_segment_change = 0.0


def _safe_channel(channel):
    return str(channel).replace(".", "_").replace("/", "_")


def live_buffer_keep_segments():
    """Return the number of HLS segments kept in the live rewind buffer.

    The stored setting is authoritative. Do not remap it on every read.
    """

    try:
        value = int(
            database.get_setting(
                "live_buffer_segments",
                str(DEFAULT_KEEP_SEGMENTS),
            )
            or DEFAULT_KEEP_SEGMENTS
        )
    except Exception:
        return DEFAULT_KEEP_SEGMENTS

    return max(1, value)


def live_buffer_retention_seconds():
    """Return the live-TV rewind window in seconds.

    This is retained for callers that need the live-buffer duration expressed
    in seconds. It must not be used for Background DVR program expiration.
    """

    return live_buffer_keep_segments() * HLS_SEGMENT_SECONDS


def background_dvr_retention_mode():
    """Return the selected retention policy for unsaved DVR programs."""

    try:
        value = str(
            database.get_setting(
                "background_dvr_retention",
                DEFAULT_BACKGROUND_DVR_RETENTION,
            )
            or DEFAULT_BACKGROUND_DVR_RETENTION
        ).strip().lower()
    except Exception:
        return DEFAULT_BACKGROUND_DVR_RETENTION

    if value not in BACKGROUND_DVR_RETENTION_OPTIONS:
        return DEFAULT_BACKGROUND_DVR_RETENTION

    return value


def background_dvr_retention_seconds():
    """Return fixed retention seconds for applicable DVR policies.

    A return value of None means the policy requires special handling:

    end_of_day:
        Calculate expiration as the next local midnight.

    never:
        Do not assign an automatic expiration time.
    """

    mode = background_dvr_retention_mode()

    fixed_retention = {
        "1_day": 24 * 60 * 60,
        "3_days": 3 * 24 * 60 * 60,
        "7_days": 7 * 24 * 60 * 60,
        "14_days": 14 * 24 * 60 * 60,
    }

    return fixed_retention.get(mode)


def live_start_min_segments():
    """
    How many HLS segments must exist before the /timeshift/start API
    responds to the app with a playable URL.

    Each segment is HLS_SEGMENT_SECONDS long (2s), so this is a direct
    trade-off between channel-change speed and initial buffer safety
    margin: min_segments=4 (8s of pre-buffered content) was overly
    conservative and added ~4-6s to every channel change on top of tuner
    lock and ffmpeg start-up time. min_segments=2 (4s of content) still
    gives ExoPlayer's own bufferForPlaybackMs floor something to chew on
    immediately, and ffmpeg keeps writing new segments in the background
    regardless, so the player catches up to real-time within a second or
    two even if it starts a bit thin.
    """
    try:
        value = database.get_setting("live_start_min_segments", "2")
        return max(1, int(value or 2))
    except Exception:
        return 2


def live_start_ready_timeout():
    try:
        value = database.get_setting("live_start_ready_timeout", "10")
        return max(3, int(value or 10))
    except Exception:
        return 10


def get_current_program(channel):
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        return database.get_current_program(channel, now)
    except Exception:
        return None


def _reset_watchdog_state(session_id=None):
    global _watchdog_session_id
    global _last_segment
    global _last_segment_mtime
    global _last_segment_change

    _watchdog_session_id = session_id
    _last_segment = None
    _last_segment_mtime = 0.0
    _last_segment_change = time.time()



def _latest_segment_number(session):
    segments = session.segment_files()
    if not segments:
        return -1

    try:
        return int(segments[-1].stem.rsplit("_", 1)[1])
    except (IndexError, TypeError, ValueError):
        return -1


def _remove_path(path):
    path = Path(path)

    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)
    except Exception as e:
        print(f"Live Watchdog: cleanup warning path={path} error={e}", flush=True)


def _ensure_session_link(canonical_dir):
    """Convert the canonical session directory to a stable symlink once.

    The URL-facing path remains unchanged. Its symlink target can then be
    replaced atomically during every future recovery.
    """

    canonical_dir = Path(canonical_dir)

    if canonical_dir.is_symlink():
        return canonical_dir.resolve()

    generation_dir = canonical_dir.with_name(
        f"{canonical_dir.name}.generation-initial"
    )

    _remove_path(generation_dir)

    if canonical_dir.exists():
        canonical_dir.rename(generation_dir)
    else:
        generation_dir.mkdir(parents=True, exist_ok=True)

    temp_link = canonical_dir.with_name(
        f".{canonical_dir.name}.link-{time.time_ns()}"
    )
    temp_link.symlink_to(generation_dir.name, target_is_directory=True)
    os.replace(temp_link, canonical_dir)

    return generation_dir


def _switch_session_link(canonical_dir, generation_dir):
    canonical_dir = Path(canonical_dir)
    generation_dir = Path(generation_dir)

    temp_link = canonical_dir.with_name(
        f".{canonical_dir.name}.link-{time.time_ns()}"
    )
    temp_link.symlink_to(generation_dir.name, target_is_directory=True)
    os.replace(temp_link, canonical_dir)


def _recover_live_session(channel, reason):
    """Recover live TV while keeping the public playlist path available."""

    global _watchdog_recovering
    global _watchdog_last_recovery

    now = time.time()

    if _watchdog_recovering:
        print(
            f"Live Watchdog: recovery skipped; already recovering  "
            f"channel={channel}",
            flush=True,
        )
        return False

    cooldown_remaining = WATCHDOG_RECOVERY_COOLDOWN - (
        now - _watchdog_last_recovery
    )

    if cooldown_remaining > 0:
        print(
            f"Live Watchdog: recovery skipped; cooldown active  "
            f"channel={channel}  "
            f"remaining={cooldown_remaining:.1f}s",
            flush=True,
        )
        return False

    _watchdog_recovering = True
    _watchdog_last_recovery = now

    print(
        f"Live Watchdog: seamless recovery starting  "
        f"channel={channel}  "
        f"reason={reason}",
        flush=True,
    )

    replacement = None
    replacement_dir = None

    try:
        with _live_session_lock:
            active = get_active()
            if not active:
                raise RuntimeError("active session disappeared")

            if str(active.get("channel")) != str(channel):
                raise RuntimeError("active channel changed during recovery")

            ch = database.get_channel(channel)
            if not ch:
                raise RuntimeError("channel not found")

            old_session = make_session_from_active(active)
            canonical_dir = Path(active["session_dir"])

            # Keep the existing playlist and segments available to Media3 while
            # FFmpeg is restarted. Stopping FFmpeg does not remove those files.
            live_segments.stop_segment_monitor()
            live_segments.close_active_segment(active)
            old_session.stop()

            old_generation = _ensure_session_link(canonical_dir)
            next_segment = _latest_segment_number(
                StreamSession(
                    session_id=active["session_id"],
                    channel=active["channel"],
                    guide_name=active.get("guide_name", active["channel"]),
                    source_url=active["source_url"],
                    session_dir=old_generation,
                )
            ) + 1

            replacement_dir = canonical_dir.with_name(
                f"{canonical_dir.name}.generation-{time.time_ns()}"
            )
            _remove_path(replacement_dir)

            tuner_source = str(
                ch.get("_tuner_source")
                or active.get(
                    "tuner_source",
                    "hdhomerun",
                )
            ).strip().lower()

            source_type = (
                "native_dvb"
                if tuner_source
                == "native_dvb"
                else "url"
            )

            replacement = StreamSession(
                session_id=active["session_id"],
                channel=ch["guide_number"],
                guide_name=ch["guide_name"],
                source_url=ch["url"],
                session_dir=replacement_dir,
                start_number=max(
                    0,
                    next_segment,
                ),
                discontinuity_start=True,
                source_type=source_type,
                native_dvb_adapter=(
                    active.get(
                        "native_dvb_adapter"
                    )
                ),
            )

            replacement.write_metadata(
                program=get_current_program(ch["guide_number"])
            )

            if not replacement.start():
                raise RuntimeError(
                    "replacement FFmpeg did not create a playlist"
                )

            ready = replacement.wait_until_ready(
                min_segments=live_start_min_segments(),
                timeout_seconds=live_start_ready_timeout(),
            )

            if not ready.get("ready"):
                raise RuntimeError(
                    "replacement stream did not reach startup readiness"
                )

            index.rebuild_index_from_files(replacement_dir)
            metadata.attach_program(
                replacement_dir,
                ch["guide_number"],
            )

            # Atomic symlink replacement keeps the URL-facing directory present.
            _switch_session_link(canonical_dir, replacement_dir)

            active_data = {
                "session_id": active["session_id"],
                "channel": ch["guide_number"],
                "guide_name": ch["guide_name"],
                "source_url": ch["url"],
                "source_type": replacement.source_type,
                "tuner_source": tuner_source,
                "native_dvb_adapter": (
                    replacement.native_dvb_adapter
                ),
                "session_dir": str(canonical_dir),
                "playlist_url": (
                    f"/livebuffer/sessions/"
                    f"{active['session_id']}/live.m3u8"
                ),
                "started_at": datetime.datetime.now().isoformat(),
                "ready": True,
                "ready_status": ready,
            }
            save_active(active_data)

            live_segments.sync_active_segment(active_data)
            live_segments.start_segment_monitor(active_data)

            _reset_watchdog_state(str(active_data["session_id"]))

            print(
                f"Live Watchdog: seamless recovery successful  "
                f"channel={channel}  "
                f"segment={ready.get('latest_segment', 'unknown')}",
                flush=True,
            )

            # The old generation is no longer URL-facing. Leave it briefly so
            # any in-flight HTTP reads can finish, then remove it asynchronously.
            def cleanup_old_generation():
                time.sleep(30)
                _remove_path(old_generation)

            threading.Thread(
                target=cleanup_old_generation,
                name="signaldvr-old-live-cleanup",
                daemon=True,
            ).start()

            return True

    except Exception as e:
        if replacement is not None:
            try:
                replacement.stop()
            except Exception:
                pass

        if replacement_dir is not None:
            _remove_path(replacement_dir)

        print(
            f"Live Watchdog: seamless recovery failed  "
            f"channel={channel}  "
            f"error={e}",
            flush=True,
        )
        return False

    finally:
        _watchdog_recovering = False

def _watchdog_loop():
    global _watchdog_session_id
    global _last_segment
    global _last_segment_mtime
    global _last_segment_change

    print("Live Watchdog started.", flush=True)

    while True:
        time.sleep(WATCHDOG_INTERVAL)

        try:
            active = get_active()

            if not active:
                _reset_watchdog_state()
                continue

            session_id = str(active.get("session_id") or "")

            # A channel/session change starts a fresh progress baseline.
            if session_id != _watchdog_session_id:
                _reset_watchdog_state(session_id)

            session = make_session_from_active(active)

            if not session.is_running():
                print(
                    f"Live Watchdog: ffmpeg is not running  "
                    f"channel={session.channel}",
                    flush=True,
                )

                _recover_live_session(
                    session.channel,
                    reason="ffmpeg_not_running",
                )
                continue

            segments = session.segment_files()

            if not segments:
                age = time.time() - _last_segment_change

                if age >= STALL_TIMEOUT:
                    print(
                        f"Live Watchdog: STREAM STALLED  "
                        f"channel={session.channel}  "
                        f"segment=none  "
                        f"stall={age:.1f}s",
                        flush=True,
                    )

                    _recover_live_session(
                        session.channel,
                        reason=f"no_segments_{age:.1f}s",
                    )
                continue

            newest_path = segments[-1]

            try:
                newest_mtime = newest_path.stat().st_mtime
            except (FileNotFoundError, OSError):
                # The file may have rotated between listing and stat.
                continue

            newest_name = newest_path.name
            now = time.time()

            progress = (
                newest_name != _last_segment
                or newest_mtime != _last_segment_mtime
            )

            if progress:
                _last_segment = newest_name
                _last_segment_mtime = newest_mtime
                _last_segment_change = now

                continue

            stall_time = now - _last_segment_change

            if stall_time >= STALL_TIMEOUT:
                print(
                    f"Live Watchdog: STREAM STALLED  "
                    f"channel={session.channel}  "
                    f"segment={newest_name}  "
                    f"stall={stall_time:.1f}s",
                    flush=True,
                )

                _recover_live_session(
                    session.channel,
                    reason=f"segment_stalled_{stall_time:.1f}s",
                )

        except Exception as e:
            print(
                f"Watchdog exception: {e}",
                flush=True,
            )


def start_live_watchdog():
    global _watchdog_started
    global _watchdog_thread

    if _watchdog_started:
        return

    _watchdog_started = True

    _watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        name="signaldvr-live-watchdog",
        daemon=True,
    )

    _watchdog_thread.start()


def get_active():
    if not ACTIVE_FILE.exists():
        return None

    try:
        return json.loads(ACTIVE_FILE.read_text())
    except Exception:
        return None


def save_active(data):
    config.LIVEBUFFER.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(json.dumps(data, indent=2))


def clear_active():
    ACTIVE_FILE.unlink(missing_ok=True)


def make_session_from_active(active):
    return StreamSession(
        session_id=active["session_id"],
        channel=active["channel"],
        guide_name=active.get(
            "guide_name",
            active["channel"],
        ),
        source_url=active["source_url"],
        session_dir=Path(
            active["session_dir"]
        ),
        source_type=active.get(
            "source_type",
            "url",
        ),
        native_dvb_adapter=active.get(
            "native_dvb_adapter"
        ),
    )



def stop_active():
    with _live_session_lock:
        active = get_active()

        if not active:
            return True

        try:
            live_segments.stop_segment_monitor()
            live_segments.close_active_segment(active)
            session = make_session_from_active(active)
            session.stop()
        except Exception as e:
            print("stop_active error:", e, flush=True)

        clear_active()
        return True


def start_or_reuse(channel):
    _t0 = time.time()
    with _live_session_lock:
        ch = database.get_channel(channel)
        _t_get_channel = time.time()

        if not ch:
            raise RuntimeError("Channel not found")

        active = get_active()

        # Reuse requires BOTH the same channel and the same currently
        # selected physical tuner source.
        #
        # Example:
        #   active session = HDHomeRun 17.1
        #   current mode   = Native DVB only
        #
        # In that case the old HDHomeRun session must be stopped and a
        # fresh Native DVB session started instead of reusing it.
        requested_tuner_source = str(
            ch.get("_tuner_source")
            or "hdhomerun"
        ).strip().lower()

        active_tuner_source = str(
            (active or {}).get(
                "tuner_source",
                "hdhomerun",
            )
        ).strip().lower()

        same_channel = bool(
            active
            and str(active.get("channel"))
            == str(ch["guide_number"])
        )

        same_tuner_source = bool(
            active
            and active_tuner_source
            == requested_tuner_source
        )

        if same_channel and same_tuner_source:
            session = make_session_from_active(active)

            if session.is_running() and session.playlist.exists():
                ready = session.wait_until_ready(
                    min_segments=live_start_min_segments(),
                    timeout_seconds=live_start_ready_timeout(),
                )
                _t_ready = time.time()

                index.rebuild_index_from_files(session.session_dir)
                metadata.attach_program(
                    session.session_dir,
                    ch["guide_number"],
                )
                _t_index = time.time()

                buffer.cleanup_segments(
                    session.session_dir,
                    keep_segments=live_buffer_keep_segments(),
                )
                _t_cleanup = time.time()

                active["ready"] = bool(ready.get("ready"))
                active["ready_status"] = ready
                save_active(active)

                live_segments.sync_active_segment(active)
                live_segments.start_segment_monitor(active)
                _t_segsync = time.time()

                print(
                    "CHANNEL_CHANGE_TIMING (reuse) channel=%s "
                    "get_channel=%.3fs wait_ready=%.3fs index+meta=%.3fs "
                    "cleanup=%.3fs segsync=%.3fs TOTAL=%.3fs" % (
                        channel,
                        _t_get_channel - _t0,
                        _t_ready - _t_get_channel,
                        _t_index - _t_ready,
                        _t_cleanup - _t_index,
                        _t_segsync - _t_cleanup,
                        _t_segsync - _t0,
                    ),
                    flush=True,
                )

                return active

        _t_stop_start = time.time()
        stop_active()
        _t_stop_done = time.time()

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        session_id = _safe_channel(ch["guide_number"])
        session_dir = SESSIONS_DIR / session_id

        # Recover automatically from interrupted live-session cleanup.
        if session_dir.is_symlink():
            try:
                session_dir.resolve(strict=True)
            except FileNotFoundError:
                print(
                    f"Removing broken live session link: {session_dir}",
                    flush=True,
                )
                session_dir.unlink(missing_ok=True)

        for stale in SESSIONS_DIR.glob(f"{session_id}.generation-*"):
            try:
                if not stale.exists():
                    _remove_path(stale)
            except Exception:
                pass

        tuner_source = str(
            ch.get("_tuner_source")
            or "hdhomerun"
        ).strip().lower()

        source_type = (
            "native_dvb"
            if tuner_source == "native_dvb"
            else "url"
        )

        if not ch.get("url"):
            raise RuntimeError(
                "Selected tuner source has no "
                f"playable channel {channel}"
            )

        session = StreamSession(
            session_id=session_id,
            channel=ch["guide_number"],
            guide_name=ch["guide_name"],
            source_url=ch["url"],
            session_dir=session_dir,
            source_type=source_type,
        )

        _t_precheck = time.time()

        program = get_current_program(ch["guide_number"])
        session.write_metadata(program=program)
        _t_metadata = time.time()

        started = session.start()

        # Some Linux DVB frontends need one tune cycle after being idle before
        # they reliably deliver a usable MPEG transport stream. If the first
        # Native DVB startup exits before producing HLS, cleanly release the
        # failed pipeline and retry the same channel once.
        #
        # Network tuner behavior is deliberately unchanged.
        if (
            not started
            and session.source_type == "native_dvb"
        ):
            print(
                "Native DVB cold start failed; "
                f"retrying channel={channel}",
                flush=True,
            )

            session.stop()

            # Give the frontend/driver a short moment to settle after the
            # failed tune before reopening it.
            time.sleep(0.75)

            started = session.start()

        _t_ffmpeg_start = time.time()

        if not started:
            raise RuntimeError(
                "Timed out waiting for live HLS playlist"
            )

        ready = session.wait_until_ready(
            min_segments=live_start_min_segments(),
            timeout_seconds=live_start_ready_timeout(),
        )
        _t_ready = time.time()

        if not ready.get("ready"):
            print(
                "live stream readiness timeout:",
                json.dumps(ready),
                flush=True,
            )

        index.rebuild_index_from_files(session.session_dir)
        metadata.attach_program(
            session.session_dir,
            ch["guide_number"],
        )
        _t_index = time.time()

        print(
            "CHANNEL_CHANGE_TIMING (fresh) channel=%s "
            "get_channel=%.3fs stop_old=%.3fs precheck=%.3fs "
            "write_metadata=%.3fs ffmpeg_start(to_1st_segment)=%.3fs "
            "wait_ready(to_min_segments)=%.3fs index+meta=%.3fs "
            "TOTAL=%.3fs" % (
                channel,
                _t_get_channel - _t0,
                _t_stop_done - _t_stop_start,
                _t_precheck - _t_stop_done,
                _t_metadata - _t_precheck,
                _t_ffmpeg_start - _t_metadata,
                _t_ready - _t_ffmpeg_start,
                _t_index - _t_ready,
                _t_index - _t0,
            ),
            flush=True,
        )

        active_data = {
            "session_id": session_id,
            "channel": ch["guide_number"],
            "guide_name": ch["guide_name"],
            "source_url": ch["url"],
            "source_type": session.source_type,
            "tuner_source": tuner_source,
            "native_dvb_adapter": (
                session.native_dvb_adapter
            ),
            "session_dir": str(session_dir),
            "playlist_url": session.playlist_url(),
            "started_at": datetime.datetime.now().isoformat(),
            "ready": bool(ready.get("ready")),
            "ready_status": ready,
        }

        save_active(active_data)

        # Open the first guide-aware live-buffer program as soon as playback starts.
        # This prepares SQLite for Background DVR and "save what I watched"
        # workflows without changing playback, tuner, or playlist behavior.
        try:
            live_segments.sync_active_segment(active_data)
            live_segments.start_segment_monitor(active_data)
        except Exception as e:
            print("live segment sync error:", e, flush=True)

        return active_data