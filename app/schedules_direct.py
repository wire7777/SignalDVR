import hashlib
import requests
import json
from pathlib import Path

from app import database


BASE_URL = "https://json.schedulesdirect.org/20141201"


def cooldown_ok(setting_key, hours=24):
    import datetime

    last = database.get_setting(setting_key, "")

    if not last or last == "test":
        return True

    try:
        last_dt = datetime.datetime.fromisoformat(last)
    except Exception:
        return True

    age = datetime.datetime.now() - last_dt
    return age.total_seconds() >= hours * 3600


def mark_now(setting_key):
    import datetime

    database.set_setting(
        setting_key,
        datetime.datetime.now().isoformat(timespec="seconds")
    )

def get_token():
    if not cooldown_ok("sd_token_created", hours=20):
        token = database.get_setting("sd_token", "")
        if token:
            return token

    token = login()

    database.set_setting("sd_token", token)
    mark_now("sd_token_created")

    return token

def get_credentials():
    username = database.get_setting("sd_username", "")
    password = database.get_setting("sd_password", "")

    if not username or not password:
        raise RuntimeError("Schedules Direct username/password missing in Settings.")

    return username, password


def login():
    username, password = get_credentials()

    password_hash = hashlib.sha1(password.encode("utf-8")).hexdigest()

    response = requests.post(
        f"{BASE_URL}/token",
        json={
            "username": username,
            "password": password_hash,
        },
        timeout=30,
    )

    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(
            f"Schedules Direct login failed: code={data.get('code')} response={data.get('response')}"
        )

    return data["token"]


def update():
    postal_code = database.get_setting("sd_postal_code", "")
    lineup = database.get_setting("sd_lineup", "")
    days = database.get_setting("guide_days", "14")

    raise RuntimeError(
        "Schedules Direct importer is not implemented yet. "
        f"postal_code={postal_code or '-'} "
        f"lineup={lineup or '-'} "
        f"days={days}"
    )



def add_lineup(lineup_id):
    token = get_token()

    response = requests.put(
        f"{BASE_URL}/lineups/{lineup_id}",
        headers={"token": token},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Unable to add lineup: HTTP {response.status_code}"
        )

    return response.json()

def flatten_lineups(headends):
    rows = []

    for h in headends:
        for lineup in h.get("lineups", []):
            rows.append({
                "lineup": lineup.get("lineup", ""),
                "name": lineup.get("name", ""),
                "location": h.get("location", ""),
                "transport": h.get("transport", ""),
                "uri": lineup.get("uri", ""),
                "headend": h.get("headend", ""),
            })

    return rows

def add_lineup(lineup_id):
    token = get_token()

    response = requests.put(
        f"{BASE_URL}/lineups/{lineup_id}",
        headers={"token": token},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Unable to add lineup: HTTP {response.status_code}"
        )

    return response.json()



def get_lineups(force=False):
    if not force and not cooldown_ok("sd_last_lineup_refresh", hours=24):
        raise RuntimeError(
            "Schedules Direct lineup lookup skipped: cooldown active. "
            "Try again later or use force=True."
        )

    country = database.get_setting("sd_country", "USA") or "USA"
    postal_code = database.get_setting("sd_postal_code", "")

    if not postal_code:
        raise RuntimeError("Schedules Direct ZIP/postal code missing in Settings.")

    token = get_token()

    response = requests.get(
    f"{BASE_URL}/headends",
    headers={"token": token},
    params={
        "country": country,
        "postalcode": postal_code,
    },
    timeout=30,
)

    if response.status_code != 200:
        raise RuntimeError(
            f"Schedules Direct lineup lookup HTTP error: {response.status_code}"
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "Schedules Direct lineup lookup failed: non-JSON response"
        )

    if isinstance(data, dict) and data.get("code") not in (None, 0):
        raise RuntimeError(
            f"Schedules Direct lineup lookup failed: "
            f"code={data.get('code')} response={data.get('response')}"
        )

    mark_now("sd_last_lineup_refresh")
    return data


def get_lineup_map(lineup_id=None):
    lineup_id = lineup_id or database.get_setting("sd_lineup", "")

    if not lineup_id:
        raise RuntimeError("Schedules Direct lineup is not selected.")

    token = get_token()

    response = requests.get(
        f"{BASE_URL}/lineups/{lineup_id}",
        headers={"token": token},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Unable to download lineup map: HTTP {response.status_code}"
        )

    return response.json()

def cache_lineup_map(lineup_id=None):
    lineup_id = lineup_id or database.get_setting("sd_lineup", "")

    if not lineup_id:
        raise RuntimeError("Schedules Direct lineup is not selected.")

    data = get_lineup_map(lineup_id)

    cache_dir = Path("guide")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "schedules_direct_lineup_map.json"

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)

    mark_now("sd_last_lineup_map_download")

    return cache_file

def import_cached_channel_map():
    import json
    from pathlib import Path

    cache_file = Path("guide") / "schedules_direct_lineup_map.json"

    if not cache_file.exists():
        raise RuntimeError("Schedules Direct lineup map cache not found.")

    with open(cache_file) as f:
        data = json.load(f)

    stations = {
        str(s.get("stationID", "")): s
        for s in data.get("stations", [])
    }

    with database.connect() as db:
        db.execute("DELETE FROM sd_channel_map")

        for m in data.get("map", []):
            guide_number = str(m.get("channel", ""))
            station_id = str(m.get("stationID", ""))

            station = stations.get(station_id, {})

            db.execute("""
                INSERT OR REPLACE INTO sd_channel_map
                (guide_number, station_id, callsign, name, raw_json)
                VALUES (?, ?, ?, ?, ?)
            """, (
                guide_number,
                station_id,
                station.get("callsign", ""),
                station.get("name", ""),
                json.dumps({
                    "map": m,
                    "station": station,
                }),
            ))

        db.commit()

    return len(data.get("map", []))