import json
import urllib.request
from app import config
from app import database


def import_lineup():
    host = database.get_setting("hdhr_host", "192.168.2.158").strip() or "192.168.2.158"
    url = f"http://{host}/lineup.json"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        # fallback to IP discovery later if .local fails
        raise

    count = 0

    for ch in data:
        guide_number = ch.get("GuideNumber")
        guide_name = ch.get("GuideName")
        stream_url = ch.get("URL")

        if guide_number and stream_url:
            database.upsert_channel(guide_number, guide_name, stream_url, source="hdhomerun")
            count += 1

    return count
