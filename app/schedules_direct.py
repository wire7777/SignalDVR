import hashlib
import requests
import json
from pathlib import Path
from app import config
from app import database


BASE_URL = "https://json.schedulesdirect.org/20141201"

# Cached Schedules Direct program images served by Flask.
PROGRAM_ARTWORK_DIR = (Path(config.THUMBNAILS) / "program_artwork")
PROGRAM_ARTWORK_DIR.mkdir(parents=True, exist_ok=True)


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

def _extract_gracenote_episode_numbers(program_metadata):
    """
    Return (season_number, episode_number) from Schedules Direct metadata.

    Schedules Direct stores episode numbering in entries such as:
        "metadata": [{"Gracenote": {"season": 1, "episode": 5}}]

    Missing or malformed values are returned as 0 and an empty string.
    """
    season_number = 0
    episode_number = ""

    metadata_items = program_metadata.get("metadata", []) or []

    if not isinstance(metadata_items, list):
        return season_number, episode_number

    for metadata_item in metadata_items:
        if not isinstance(metadata_item, dict):
            continue

        gracenote = metadata_item.get("Gracenote")

        if not isinstance(gracenote, dict):
            continue

        try:
            season_number = int(gracenote.get("season") or 0)
        except (TypeError, ValueError):
            season_number = 0

        raw_episode = gracenote.get("episode")

        if raw_episode not in (None, ""):
            episode_number = str(raw_episode)

        break

    return season_number, episode_number


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

                season_number, episode_number = (
                    _extract_gracenote_episode_numbers(meta)
                )

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
                        show_type, entity_type, genres,
                        season, episode_title, runtime, year, language,
                        cast, directors, writers, artwork
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    channel,
                    title,
                    "",
                    description,
                    start,
                    stop,
                    category,
                    episode_number,
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
                    season_number,
                    meta.get("episodeTitle150", ""),
                    duration,
                    int(str(meta.get("originalAirDate", "0"))[:4] or 0) if meta.get("originalAirDate") else 0,
                    "",
                    ",".join([c.get("name", "") for c in meta.get("cast", [])[:8]]),
                    ",".join([c.get("name", "") for c in meta.get("crew", []) if c.get("role") == "Director"][:5]),
                    ",".join([c.get("name", "") for c in meta.get("crew", []) if c.get("role") == "Writer"][:5]),
                    str(meta.get("hasImageArtwork", "")),
                ))
                inserted += 1

        db.commit()

    database.apply_series_rules()

    return inserted


def _safe_program_id(program_id):
    """Return a filesystem-safe Schedules Direct program ID."""
    value = "".join(
        character
        for character in str(program_id or "").strip()
        if character.isalnum() or character in ("-", "_")
    )
    return value[:80]


def _artwork_score(item):
    """Prefer a primary portrait poster, then other useful large images."""
    category = str(item.get("category") or "").lower()
    aspect = str(item.get("aspect") or "").lower()
    tier = str(item.get("tier") or "").lower()
    size = str(item.get("size") or "").lower()
    primary = str(item.get("primary") or "").lower() in ("true", "1", "yes")

    score = 0
    if primary:
        score += 1000
    if category in ("poster art", "box art"):
        score += 500
    elif category in ("iconic", "staple", "vod art"):
        score += 300
    elif "banner" in category:
        score += 120
    if aspect in ("2x3", "3x4"):
        score += 250
    elif aspect == "16x9":
        score += 100
    if tier == "episode":
        score += 80
    elif tier == "season":
        score += 60
    elif tier == "series":
        score += 40
    score += {"ms": 50, "lg": 40, "md": 30, "sm": 20, "xs": 10}.get(size, 0)

    try:
        score += min(int(item.get("height") or 0), 2000) // 50
    except (TypeError, ValueError):
        pass

    return score


def cache_program_artwork(program_id):
    """Download and cache the best Schedules Direct image for a program."""

    safe_id = _safe_program_id(program_id)
    if not safe_id:
        return None

    cached = sorted(PROGRAM_ARTWORK_DIR.glob(f"{safe_id}.*"))
    if cached:
        return cached[0].name

    metadata_path = Path(config.GUIDE) / "schedules_direct_programs.json"
    resource_id = ""

    try:
        if metadata_path.exists():
            programs = json.loads(metadata_path.read_text())

            for program in programs:
                if str(program.get("programID") or "") == safe_id:
                    resource_id = str(
                        program.get("resourceID") or ""
                    ).strip()
                    break
    except Exception as error:
        print(
            f"Unable to read cached program metadata for {safe_id}: {error}",
            flush=True,
        )

    lookup_ids = [safe_id]

    if resource_id and resource_id not in lookup_ids:
        lookup_ids.append(resource_id)

    token = get_token()
    images = []

    for lookup_id in lookup_ids:
        response = requests.post(
            f"{BASE_URL}/metadata/programs/",
            headers={
                "token": token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=[lookup_id],
            timeout=30,
        )

        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, list) or not payload:
            continue

        entry = payload[0]
        data = entry.get("data")

        if isinstance(data, list) and data:
            images = data
            break

        if isinstance(data, dict):
            print(
                f"Schedules Direct artwork lookup {lookup_id}: "
                f"{data.get('response')} {data.get('message')}",
                flush=True,
            )

    candidates = [
        image
        for image in images
        if isinstance(image, dict) and image.get("uri")
    ]

    if not candidates:
        return None

    selected = max(candidates, key=_artwork_score)

    image_url = str(selected.get("uri") or "").strip()
    if not image_url:
        return None

    if image_url.startswith("//"):
        image_url = "https:" + image_url
    elif not image_url.startswith(("http://", "https://")):
        image_url = BASE_URL.rstrip("/") + "/" + image_url.lstrip("/")

    image_response = requests.get(
        image_url,
        headers={"token": token},
        timeout=45,
        allow_redirects=True,
    )

    image_response.raise_for_status()

    content_type = (
        image_response.headers
        .get("Content-Type", "")
        .split(";", 1)[0]
        .lower()
    )

    extension = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type)

    if extension is None:
        suffix = Path(
            image_url.split("?", 1)[0]
        ).suffix.lower()

        extension = (
            suffix
            if suffix in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".gif",
            )
            else ".jpg"
        )

    destination = PROGRAM_ARTWORK_DIR / f"{safe_id}{extension}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    temporary.write_bytes(image_response.content)
    temporary.replace(destination)

    return destination.name