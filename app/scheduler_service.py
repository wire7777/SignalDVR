import time
import threading
from datetime import datetime, timedelta

from app import database
from app import tuner_manager
from app.recorder import Recorder


CHECK_INTERVAL = 30
START_PADDING_SECONDS = 120
STOP_PADDING_SECONDS = 300
DEBUG = False

_running = False
_thread = None

# One Recorder instance per scheduled recording ID
_recorders = {}


def _now_key():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _parse_time(value):
    return datetime.strptime(str(value)[:14], "%Y%m%d%H%M%S")


def _debug(*args):
    if DEBUG:
        print(*args, flush=True)


def _start_recording(item):
    ch = database.get_channel(item["channel"])

    if not ch:
        print("Scheduler failed: channel not found", item["channel"], flush=True)
        database.fail_scheduled_recording(item["id"], "Failed - No Channel")
        return

    tuner_id = tuner_manager.allocate(
        item["channel"],
        purpose="recording",
        title=item["title"]
    )

    if tuner_id is None:
        print("Scheduler failed: no tuner available", item["title"], flush=True)
        database.fail_scheduled_recording(item["id"], "Failed - No Tuner")
        return

    print(
        "Scheduler starting recording:",
        item["id"],
        item["title"],
        item["channel"],
        "using tuner",
        tuner_id,
        flush=True
    )

    recorder = Recorder()
    _recorders[item["id"]] = {
        "recorder": recorder,
        "tuner_id": tuner_id,
        "channel": item["channel"],
        "title": item["title"],
    }

    recorder.start_from_channel(ch)
    database.update_schedule_status(item["id"], "Recording")


def _stop_recording(item):
    job = _recorders.get(item["id"])

    print(
        "Scheduler stopping recording:",
        item["id"],
        item["title"],
        item["channel"],
        flush=True
    )

    if job:
        try:
            job["recorder"].stop()
        except Exception as e:
            print("Recorder stop error:", e, flush=True)

        tuner_manager.release(job["tuner_id"])
        _recorders.pop(item["id"], None)
    else:
        tuner_manager.release_channel(item["channel"])

    database.update_schedule_status(item["id"], "Recorded")


def scheduler_loop():
    global _running

    print("SignalDVR scheduler started", flush=True)

    while _running:
        try:
            now_key = _now_key()
            now = datetime.now()
            database.recover_stale_recordings(now_key)

            _debug("Scheduler tick", now_key)

            database.expire_old_scheduled_recordings(now_key)

            # Stop completed active recordings
            active_items = database.list_active_schedules()

            for item in active_items:
                stop = _parse_time(item["stop"]) + timedelta(seconds=STOP_PADDING_SECONDS)

                _debug(
                    "Active schedule:",
                    item["id"],
                    item["title"],
                    item["channel"],
                    "stop at",
                    stop.strftime("%Y%m%d%H%M%S")
                )

                if now >= stop:
                    _stop_recording(item)

            # Start due scheduled recordings
            scheduled_items = database.list_scheduled_recordings()

            for item in scheduled_items:
                if item["status"] != "Scheduled":
                    continue

                start = _parse_time(item["start"]) - timedelta(seconds=START_PADDING_SECONDS)
                stop = _parse_time(item["stop"]) + timedelta(seconds=STOP_PADDING_SECONDS)

                _debug(
                    "Checking schedule:",
                    item["id"],
                    item["title"],
                    item["channel"],
                    "window",
                    start.strftime("%Y%m%d%H%M%S"),
                    "-",
                    stop.strftime("%Y%m%d%H%M%S")
                )

                if start <= now < stop:
                    _start_recording(item)

        except Exception as e:
            print("Scheduler error:", e, flush=True)

        time.sleep(CHECK_INTERVAL)


def start_scheduler():
    global _running, _thread

    if _running:
        _debug("SignalDVR scheduler already running")
        return

    _running = True

    _thread = threading.Thread(
        target=scheduler_loop,
        daemon=True
    )

    _thread.start()


def stop_scheduler():
    global _running

    print("SignalDVR scheduler stopping", flush=True)
    _running = False