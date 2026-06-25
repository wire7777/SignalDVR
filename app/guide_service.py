import time
import threading
from app import database

from app import epg


CHECK_INTERVAL = 6 * 60 * 60  # every 6 hours

_running = False
_thread = None


def guide_loop():
    global _running

    print("SignalDVR guide updater started", flush=True)

    while _running:
        try:
            print("Updating guide data...", flush=True)
            epg.update_guide()
            database.apply_series_rules()
            print("Guide update complete", flush=True)
        except Exception as e:
            print("Guide update error:", e, flush=True)

        time.sleep(CHECK_INTERVAL)


def start_guide_updater():
    global _running, _thread

    if _running:
        return

    _running = True
    _thread = threading.Thread(target=guide_loop, daemon=True)
    _thread.start()


def stop_guide_updater():
    global _running
    _running = False
