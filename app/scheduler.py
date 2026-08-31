import datetime
from app import database


def parse_xmltv_time(value):
    # Example: 20260621160000 -0700
    return datetime.datetime.strptime(value[:14], "%Y%m%d%H%M%S")


def get_due_recordings():
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    scheduled = database.list_scheduled_recordings()

    due = []

    for r in scheduled:
        start = r["start"][:14]
        stop = r["stop"][:14]

        if r["status"] == "Scheduled" and start <= now < stop:
            due.append(r)

    return due