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
    """
    Full Schedules Direct guide update pipeline.
    Used by background guide updater.
    Respects cooldowns, but imports cached data if downloads are skipped.
    """
    try:
        cache_lineup_map()
    except Exception as e:
        print(f"Schedules Direct lineup map cache skipped: {e}", flush=True)

    channel_count = import_cached_channel_map()

    days = int(database.get_setting("guide_days", "14") or 14)

    try:
        cache_schedules_for_matched_channels(days=days)
    except Exception as e:
        print(f"Schedules Direct schedule cache skipped: {e}", flush=True)

    try:
        program_cache, program_id_count = cache_program_metadata()
    except Exception as e:
        print(f"Schedules Direct program metadata cache skipped: {e}", flush=True)
        program_id_count = 0

    program_count = import_cached_programs()

    print(
        f"Schedules Direct background update complete: "
        f"channels={channel_count} "
        f"program_ids={program_id_count} "
        f"programs={program_count}",
        flush=True,
    )

    return program_count



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

def cache_lineup_map(lineup_id=None, force=False):
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

def cache_schedules_for_matched_channels(days=1, force=False):
    import datetime
    import json
    from pathlib import Path

    if not force and not cooldown_ok("sd_last_schedule_download", hours=12):
        raise RuntimeError("Schedules Direct schedule download skipped: cooldown active.")

    token = get_token()

    with database.connect() as db:
        rows = db.execute("""
            SELECT DISTINCT station_id
            FROM sd_channel_map
            WHERE station_id IS NOT NULL
              AND station_id != ''
            ORDER BY station_id
        """).fetchall()

    station_ids = [str(r["station_id"]) for r in rows]

    if not station_ids:
        raise RuntimeError("No matched Schedules Direct station IDs found.")

    today = datetime.date.today()
    dates = [
        (today + datetime.timedelta(days=i)).isoformat()
        for i in range(days)
    ]

    payload = [
        {
            "stationID": station_id,
            "date": dates,
        }
        for station_id in station_ids
    ]

    response = requests.post(
        f"{BASE_URL}/schedules",
        headers={"token": token},
        json=payload,
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Schedules Direct schedule download failed: HTTP {response.status_code}"
        )

    data = response.json()

    cache_dir = Path("guide")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "schedules_direct_schedules.json"

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)

    mark_now("sd_last_schedule_download")

    return cache_file

def cache_program_metadata(force=False):
    import json
    from pathlib import Path

    if not force and not cooldown_ok("sd_last_program_download", hours=12):
        raise RuntimeError("Schedules Direct program metadata download skipped: cooldown active.")

    schedules_file = Path("guide") / "schedules_direct_schedules.json"

    if not schedules_file.exists():
        raise RuntimeError("Schedules Direct schedules cache not found.")

    with open(schedules_file) as f:
        schedules = json.load(f)

    program_ids = sorted({
        p.get("programID")
        for station in schedules
        for p in station.get("programs", [])
        if p.get("programID")
    })

    if not program_ids:
        raise RuntimeError("No program IDs found in schedules cache.")

    token = get_token()
    data = []
    batch_size = 400

    for i in range(0, len(program_ids), batch_size):
        batch = program_ids[i:i + batch_size]

        response = requests.post(
            f"{BASE_URL}/programs",
            headers={"token": token},
            json=batch,
            timeout=60,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Schedules Direct program metadata download failed: "
                f"HTTP {response.status_code} batch={i // batch_size + 1}"
            )

        data.extend(response.json())

    cache_file = Path("guide") / "schedules_direct_programs.json"

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)

    mark_now("sd_last_program_download")

    return cache_file, len(program_ids)
def preview_cached_program_import(limit=10):
    import datetime
    import json
    from pathlib import Path

    schedules_file = Path("guide") / "schedules_direct_schedules.json"
    programs_file = Path("guide") / "schedules_direct_programs.json"

    if not schedules_file.exists():
        raise RuntimeError("Schedules cache not found.")

    if not programs_file.exists():
        raise RuntimeError("Program metadata cache not found.")

    with open(schedules_file) as f:
        schedules = json.load(f)

    with open(programs_file) as f:
        program_data = json.load(f)

    programs_by_id = {
        p.get("programID"): p
        for p in program_data
        if p.get("programID")
    }

    station_to_channel = {}

    with database.connect() as db:
        rows = db.execute("""
            SELECT guide_number, station_id
            FROM sd_channel_map
        """).fetchall()

    for row in rows:
        station_to_channel[str(row["station_id"])] = row["guide_number"]

    preview = []

    for station in schedules:
        station_id = str(station.get("stationID", ""))
        channel = station_to_channel.get(station_id, "")

        if not channel:
            continue

        for item in station.get("programs", []):
            program_id = item.get("programID", "")
            meta = programs_by_id.get(program_id, {})

            title = ""
            titles = meta.get("titles", [])
            if titles:
                title = titles[0].get("title120", "")

            description = ""
            descriptions = meta.get("descriptions", {})
            descs = descriptions.get("description1000") or descriptions.get("description100")
            if descs:
                description = descs[0].get("description", "")

            start_utc = item.get("airDateTime", "")
            duration = int(item.get("duration", 0) or 0)

            try:
                start_dt = datetime.datetime.fromisoformat(
                    start_utc.replace("Z", "+00:00")
                )
                stop_dt = start_dt + datetime.timedelta(seconds=duration)

                local_tz = datetime.datetime.now().astimezone().tzinfo

                start_local = start_dt.astimezone(local_tz)
                stop_local = stop_dt.astimezone(local_tz)

                start = start_local.strftime("%Y%m%d%H%M%S")
                stop = stop_local.strftime("%Y%m%d%H%M%S")
            except Exception:
                start = start_utc
                stop = ""

            preview.append({
                "channel": channel,
                "title": title,
                "description": description[:80],
                "start": start,
                "stop": stop,
                "programID": program_id,
                "new": item.get("new", False),
                "video": item.get("videoProperties", []),
                "audio": item.get("audioProperties", []),
                "originalAirDate": meta.get("originalAirDate", ""),
                "genres": meta.get("genres", []),
            })

            if len(preview) >= limit:
                return preview

    return preview

def import_cached_programs():
    import datetime
    import json
    from pathlib import Path

    schedules_file = Path("guide") / "schedules_direct_schedules.json"
    programs_file = Path("guide") / "schedules_direct_programs.json"

    if not schedules_file.exists():
        raise RuntimeError("Schedules cache not found.")

    if not programs_file.exists():
        raise RuntimeError("Program metadata cache not found.")

    with open(schedules_file) as f:
        schedules = json.load(f)

    with open(programs_file) as f:
        program_data = json.load(f)

    programs_by_id = {
        p.get("programID"): p
        for p in program_data
        if p.get("programID")
    }

    with database.connect() as db:
        rows = db.execute("""
            SELECT guide_number, station_id
            FROM sd_channel_map
        """).fetchall()

        station_to_channel = {
            str(row["station_id"]): row["guide_number"]
            for row in rows
        }

        db.execute("DELETE FROM programs")

        inserted = 0

        for station in schedules:
            station_id = str(station.get("stationID", ""))
            channel = station_to_channel.get(station_id, "")

            if not channel:
                continue

            for item in station.get("programs", []):
                program_id = item.get("programID", "")
                meta = programs_by_id.get(program_id, {})

                titles = meta.get("titles", [])
                title = titles[0].get("title120", "") if titles else ""

                descriptions = meta.get("descriptions", {})
                descs = descriptions.get("description1000") or descriptions.get("description100") or []
                description = descs[0].get("description", "") if descs else ""

                genres = meta.get("genres", [])
                category = genres[0] if genres else ""

                rating = ""
                ratings = item.get("ratings", [])
                if ratings:
                    rating = ratings[0].get("code", "")

                start_utc = item.get("airDateTime", "")
                duration = int(item.get("duration", 0) or 0)

                try:
                    start_dt = datetime.datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                    stop_dt = start_dt + datetime.timedelta(seconds=duration)
                    local_tz = datetime.datetime.now().astimezone().tzinfo

                    start_local = start_dt.astimezone(local_tz)
                    stop_local = stop_dt.astimezone(local_tz)

                    start = start_local.strftime("%Y%m%d%H%M%S")
                    stop = stop_local.strftime("%Y%m%d%H%M%S")
                except Exception:
                    start = start_utc
                    stop = ""

                db.execute("""
                    INSERT INTO programs
                    (
                        channel, title, subtitle, description,
                        start, stop, category, episode, rating, is_new,
                        programid, station_id, originalairdate,
                        video_properties, audio_properties,
                        show_type, entity_type, genres
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    channel,
                    title,
                    "",
                    description,
                    start,
                    stop,
                    category,
                    "",
                    rating,
                    1 if item.get("new") else 0,
                    program_id,
                    station_id,
                    meta.get("originalAirDate", ""),
                    ",".join(item.get("videoProperties", []) or []),
                    ",".join(item.get("audioProperties", []) or []),
                    meta.get("showType", ""),
                    meta.get("entityType", ""),
                    ",".join(genres),
                ))

                inserted += 1

        db.commit()

    database.apply_series_rules()

    return inserted