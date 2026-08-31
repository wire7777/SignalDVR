import time
import threading

from app import database
from app import epg


CHECK_INTERVAL = 6 * 60 * 60  # every 6 hours

_running = False
_thread = None
_stop_event = threading.Event()


def guide_loop():
    """
    Background guide refresh loop.

    SignalDVR already has cached guide data and database records available at
    startup. Therefore, do not immediately contact the guide provider whenever
    the service restarts.

    Wait for the normal six-hour interval before attempting the first refresh.
    The existing EPG/Schedules Direct cache and cooldown behavior remains
    unchanged.
    """
    global _running

    print(
        "SignalDVR guide updater started; "
        "first scheduled update in 6 hours",
        flush=True,
    )

    while _running:
        # Wait before the first update and between later updates.
        # Event.wait() also lets shutdown interrupt the wait immediately.
        if _stop_event.wait(CHECK_INTERVAL):
            break

        if not _running:
            break

        try:
            print("Updating guide data...", flush=True)
            epg.update_guide()
            database.apply_series_rules()
            print("Guide update complete", flush=True)
        except Exception as e:
            print("Guide update error:", e, flush=True)


def start_guide_updater():
    global _running, _thread

    if _running:
        return

    _stop_event.clear()
    _running = True

    _thread = threading.Thread(
        target=guide_loop,
        daemon=True,
        name="SignalDVRGuideUpdater",
    )
    _thread.start()


def stop_guide_updater():
    global _running

    _running = False
    _stop_event.set()
