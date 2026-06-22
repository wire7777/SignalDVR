import time
import datetime

from app import database
from app import tuner
from app.recorder import Recorder


recorder = Recorder()


def now_xmltv():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def run_scheduler():
    print("AndresDVR scheduler started", flush=True)

    while True:
        now = now_xmltv()

        # Mark old missed recordings as expired.
        database.expire_old_scheduled_recordings(now)

        scheduled = database.list_scheduled_recordings()

        for item in scheduled:
            start = item["start"][:14]
            stop = item["stop"][:14]
            status = item["status"]

            # Start scheduled recording.
            if (
                status == "Scheduled"
                and start <= now < stop
                and not database.get_active_schedule()
            ):
                channel = database.get_channel(item["channel"])

                if channel and tuner.acquire():
                    print(
                        f"Starting scheduled recording: {item['title']}",
                        flush=True,
                    )

                    recorder.start_from_channel(channel)
                    database.update_schedule_status(item["id"], "Recording")

            # Stop scheduled recording.
            if status == "Recording" and now >= stop:
                print(
                    f"Stopping scheduled recording: {item['title']}",
                    flush=True,
                )

                recorder.stop()
                database.update_schedule_status(item["id"], "Recorded")
                tuner.release()

        time.sleep(15)


if __name__ == "__main__":
    run_scheduler()