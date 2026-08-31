from pathlib import Path
from threading import Event, Lock, RLock, Thread
import time

from app import timeshift
from app.stream_engine import index


SEGMENT_SECONDS = 2.0
DEFAULT_WINDOW_SEGMENTS = 900
REFRESH_SECONDS = 0.25


class DelayedLiveSession:
    """
    Continuous delayed-live HLS playlist with a playback-paced cursor.

    The cursor advances according to elapsed playback time, not according
    to how many source files appear during a filesystem scan. This prevents
    multi-segment jumps after pause/resume.
    """

    def __init__(
        self,
        session_dir,
        seconds_behind=30,
        output_name="delayed_live.m3u8",
        window_segments=DEFAULT_WINDOW_SEGMENTS,
    ):
        self.session_dir = Path(session_dir)
        self.seconds_behind = max(2, int(seconds_behind))
        self.output_name = output_name
        self.output_path = self.session_dir / output_name
        self.window_segments = max(30, int(window_segments))

        self._stop_event = Event()
        self._thread = None

        # Exclusive index of the final row exposed in the playlist.
        self._cursor_end = None

        # Monotonic timing prevents wall-clock changes from affecting playback.
        self._last_tick = None
        self._elapsed_credit = 0.0
        self._paused = False

        # Protect cursor state and playlist writes from concurrent refresh/seek calls.
        # The background refresh thread and HTTP FF/REW requests otherwise race on
        # delayed_live.m3u8.tmp, which can intermittently raise FileNotFoundError.
        self._state_lock = RLock()

    def _load_rows(self):
        rows = index.load_index(self.session_dir)

        if not rows:
            rows = index.rebuild_index_from_files(self.session_dir)

        valid_rows = []

        for row in rows or []:
            filename = row.get("file")

            if not filename:
                continue

            segment_path = self.session_dir / filename

            try:
                if not segment_path.exists():
                    continue

                # Never expose the zero-byte file currently being written.
                if segment_path.stat().st_size <= 0:
                    continue
            except OSError:
                continue

            valid_rows.append(row)

        return valid_rows

    @staticmethod
    def _sequence(row, fallback=0):
        try:
            return int(row.get("sequence", fallback))
        except (TypeError, ValueError):
            return int(fallback)

    def _initialize_cursor(self, rows):
        delay_segments = max(
            1,
            int(round(self.seconds_behind / SEGMENT_SECONDS)),
        )

        self._cursor_end = max(
            1,
            len(rows) - delay_segments,
        )

        self._last_tick = time.monotonic()
        self._elapsed_credit = 0.0

    def _update_cursor(self, rows, paused):
        now = time.monotonic()

        if self._cursor_end is None:
            self._initialize_cursor(rows)
            self._paused = paused
            return

        if paused:
            # Freeze exactly where playback was. Discard elapsed pause time,
            # so resume never tries to catch up.
            self._last_tick = now
            self._elapsed_credit = 0.0
            self._paused = True
            return

        if self._paused:
            # First refresh after resume starts a fresh playback clock.
            self._last_tick = now
            self._elapsed_credit = 0.0
            self._paused = False
            return

        if self._last_tick is None:
            self._last_tick = now
            return

        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        self._elapsed_credit += elapsed

        # Advance no faster than one segment per segment duration.
        segments_to_advance = int(
            self._elapsed_credit // SEGMENT_SECONDS
        )

        if segments_to_advance <= 0:
            return

        available_ahead = max(
            0,
            len(rows) - self._cursor_end,
        )

        actual_advance = min(
            segments_to_advance,
            available_ahead,
        )

        if actual_advance > 0:
            self._cursor_end += actual_advance
            self._elapsed_credit -= (
                actual_advance * SEGMENT_SECONDS
            )

        # Do not accumulate unlimited credit while waiting for FFmpeg.
        if actual_advance < segments_to_advance:
            self._elapsed_credit = min(
                self._elapsed_credit,
                SEGMENT_SECONDS,
            )

    def current_seconds_behind(self, rows=None):
        """Return the server-authoritative distance from the live edge."""
        if rows is None:
            rows = self._load_rows()

        if not rows:
            return self.seconds_behind

        if self._cursor_end is None:
            self._initialize_cursor(rows)

        cursor_end = max(1, min(self._cursor_end, len(rows)))
        segments_behind = max(0, len(rows) - cursor_end)
        return int(round(segments_behind * SEGMENT_SECONDS))

    def seek_relative(self, seconds):
        """
        Move the delayed-live cursor relative to its current server position.

        Positive values fast-forward; negative values rewind. The server owns
        the cursor, so clients never need to estimate how far they are behind.
        """
        with self._state_lock:
            rows = self._load_rows()

            if not rows:
                raise RuntimeError("No timeshift segments are available")

            if self._cursor_end is None:
                self._initialize_cursor(rows)

            delta_segments = int(round(float(seconds) / SEGMENT_SECONDS))

            if delta_segments == 0 and int(seconds) != 0:
                delta_segments = 1 if seconds > 0 else -1

            self._cursor_end = max(
                1,
                min(len(rows), self._cursor_end + delta_segments),
            )

            # A manual seek establishes a new playback clock at the target.
            self._last_tick = time.monotonic()
            self._elapsed_credit = 0.0
            self._paused = timeshift.is_paused()

            self._write_playlist(rows)

            seconds_behind = self.current_seconds_behind(rows)
            self.seconds_behind = seconds_behind

            return {
                "playlist": self.output_name,
                "seconds_behind": seconds_behind,
                "live": seconds_behind <= 0,
                "mode": "live" if seconds_behind <= 0 else "delayed_live",
            }

    def _playlist_rows(self, rows):
        if self._cursor_end is None:
            self._initialize_cursor(rows)

        cursor_end = max(
            1,
            min(self._cursor_end, len(rows)),
        )

        start = max(
            0,
            cursor_end - self.window_segments,
        )

        return rows[start:cursor_end]

    def _write_playlist(self, rows):
        delayed_rows = self._playlist_rows(rows)

        if not delayed_rows:
            return False

        first_sequence = self._sequence(
            delayed_rows[0],
            0,
        )

        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:3",
            f"#EXT-X-MEDIA-SEQUENCE:{first_sequence}",
        ]

        for row in delayed_rows:
            try:
                duration = float(
                    row.get("duration") or SEGMENT_SECONDS
                )
            except (TypeError, ValueError):
                duration = SEGMENT_SECONDS

            lines.append(f"#EXTINF:{duration:.3f},")
            lines.append(row["file"])

        # No EXT-X-ENDLIST: this remains an open live playlist.
        playlist_text = "\n".join(lines) + "\n"

        temp_path = self.output_path.with_suffix(
            self.output_path.suffix + ".tmp"
        )

        temp_path.write_text(playlist_text)
        temp_path.replace(self.output_path)

        return True

    def refresh(self):
        with self._state_lock:
            rows = self._load_rows()

            if not rows:
                return False

            paused = timeshift.is_paused()

            self._update_cursor(
                rows=rows,
                paused=paused,
            )

            # Preserve the exact playlist while paused.
            if paused and self.output_path.exists():
                return True

            return self._write_playlist(rows)

    def _run(self):
        index_path = self.session_dir / index.INDEX_FILE

        while not self._stop_event.is_set():
            # The live session may be removed by cleanup, a channel change, or
            # a backend restart. Once the session directory/index disappears,
            # this delayed-live worker has nothing left to refresh and must
            # terminate instead of retrying forever and flooding the journal.
            if not self.session_dir.exists() or not index_path.exists():
                print(
                    "[DelayedLive] Session removed; stopping playlist "
                    f"refresh for {self.session_dir}",
                    flush=True,
                )
                self._stop_event.set()
                break

            try:
                self.refresh()
            except FileNotFoundError:
                # The session can disappear between the existence check and
                # the index/playlist write. Treat that race as normal shutdown.
                print(
                    "[DelayedLive] Session removed during refresh; stopping "
                    f"worker for {self.session_dir}",
                    flush=True,
                )
                self._stop_event.set()
                break
            except Exception as exc:
                print(
                    f"[DelayedLive] Playlist refresh failed: {exc}",
                    flush=True,
                )

            self._stop_event.wait(REFRESH_SECONDS)

    def start(self):
        self.session_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.refresh()

        self._thread = Thread(
            target=self._run,
            name=f"delayed-live-{self.output_name}",
            daemon=True,
        )
        self._thread.start()

        return self.output_name

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        # Keep the final playlist briefly because VLC may make one last
        # request while switching back to live.m3u8.

    def status(self):
        return {
            "active": True,
            "mode": "delayed_live",
            "seconds_behind": self.current_seconds_behind(),
            "playlist": self.output_name,
            "paused": self._paused,
            "cursor_end": self._cursor_end,
            "elapsed_credit": round(self._elapsed_credit, 3),
        }


_lock = Lock()
_active_session = None


def start_delayed_live(
    session_dir,
    seconds_behind=30,
    output_name="delayed_live.m3u8",
):
    global _active_session

    with _lock:
        if _active_session is not None:
            _active_session.stop()

        _active_session = DelayedLiveSession(
            session_dir=session_dir,
            seconds_behind=seconds_behind,
            output_name=output_name,
        )

        playlist_name = _active_session.start()

        return {
            "playlist": playlist_name,
            "seconds_behind": _active_session.seconds_behind,
            "live": False,
            "mode": "delayed_live",
        }


def stop_delayed_live():
    global _active_session

    with _lock:
        if _active_session is not None:
            _active_session.stop()
            _active_session = None


def status():
    with _lock:
        if _active_session is None:
            return {
                "active": False,
                "mode": "live",
                "seconds_behind": 0,
                "paused": False,
            }

        return _active_session.status()


def seek_relative(session_dir, seconds, output_name="delayed_live.m3u8"):
    """Apply a relative FF/RW operation to the active delayed-live cursor."""
    global _active_session

    seconds = int(seconds)
    session_path = Path(session_dir)

    with _lock:
        active = _active_session

        if active is None or active.session_dir != session_path:
            if seconds >= 0:
                return {
                    "playlist": None,
                    "seconds_behind": 0,
                    "live": True,
                    "mode": "live",
                }

            active = DelayedLiveSession(
                session_dir=session_path,
                seconds_behind=abs(seconds),
                output_name=output_name,
            )
            _active_session = active
            active.start()
            result = {
                "playlist": active.output_name,
                "seconds_behind": active.current_seconds_behind(),
                "live": False,
                "mode": "delayed_live",
            }
        else:
            result = active.seek_relative(seconds)

        if result.get("live"):
            active.stop()
            _active_session = None

        return result
