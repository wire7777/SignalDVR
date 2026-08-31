import json
import re
from pathlib import Path

import requests

from app import config


TVMAZE_BASE_URL = "https://api.tvmaze.com"

PROGRAM_ARTWORK_DIR = Path(config.THUMBNAILS) / "program_artwork"

PROGRAM_ARTWORK_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

USER_AGENT = "SignalDVR/0.9 TVmazeArtwork"


def _safe_program_id(program_id):
    return re.sub(
        r"[^A-Za-z0-9_.-]",
        "_",
        str(program_id or "").strip(),
    )


def _normalized(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").lower(),
    ).strip()


def _program_cache_path():
    return (
        Path(config.GUIDE)
        / "schedules_direct_programs.json"
    )


def _find_program(program_id):
    path = _program_cache_path()

    if not path.exists():
        return None

    try:
        programs = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except Exception as error:
        print(
            f"TVmaze: unable to read program cache: {error}",
            flush=True,
        )
        return None

    wanted = str(program_id or "").strip()

    for program in programs:
        if (
            str(program.get("programID") or "").strip()
            == wanted
        ):
            return program

    return None


def _program_title(program):
    titles = program.get("titles") or []

    for title_item in titles:
        value = (
            title_item.get("title120")
            or title_item.get("title50")
            or title_item.get("title")
        )

        if value:
            return str(value).strip()

    return ""


def _program_year(program):
    air_date = str(
        program.get("originalAirDate") or ""
    ).strip()

    if len(air_date) >= 4 and air_date[:4].isdigit():
        return int(air_date[:4])

    return 0


def _match_score(show, title, year):
    show_name = str(show.get("name") or "")
    score = 0

    if _normalized(show_name) == _normalized(title):
        score += 100
    elif _normalized(title) in _normalized(show_name):
        score += 50

    premiered = str(show.get("premiered") or "")

    if (
        year > 0
        and len(premiered) >= 4
        and premiered[:4].isdigit()
    ):
        show_year = int(premiered[:4])
        difference = abs(show_year - year)

        if difference == 0:
            score += 25
        elif difference <= 2:
            score += 10

    if (show.get("image") or {}).get("original"):
        score += 20

    return score


def _find_tvmaze_show(title, year):
    response = requests.get(
        f"{TVMAZE_BASE_URL}/search/shows",
        params={"q": title},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=20,
    )

    response.raise_for_status()

    results = response.json()

    if not isinstance(results, list) or not results:
        return None

    candidates = [
        item.get("show")
        for item in results
        if isinstance(item, dict)
        and isinstance(item.get("show"), dict)
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda show: _match_score(
            show,
            title,
            year,
        ),
    )


def cache_program_artwork(program_id):
    safe_id = _safe_program_id(program_id)

    if not safe_id:
        return None

    cached = sorted(
        PROGRAM_ARTWORK_DIR.glob(
            f"{safe_id}.*"
        )
    )

    if cached:
        return cached[0].name

    program = _find_program(program_id)

    if not program:
        print(
            f"TVmaze: program not found in cache: {program_id}",
            flush=True,
        )
        return None

    entity_type = str(
        program.get("entityType") or ""
    ).lower()

    show_type = str(
        program.get("showType") or ""
    ).lower()

    # TVmaze is intended for television programs.
    if (
        "movie" in entity_type
        or "movie" in show_type
    ):
        return None

    title = _program_title(program)

    if not title:
        return None

    year = _program_year(program)

    try:
        show = _find_tvmaze_show(
            title,
            year,
        )
    except Exception as error:
        print(
            f"TVmaze search failed for "
            f"{program_id} ({title}): {error}",
            flush=True,
        )
        return None

    if not show:
        print(
            f"TVmaze: no match for {title}",
            flush=True,
        )
        return None

    image = show.get("image") or {}

    image_url = (
        image.get("original")
        or image.get("medium")
    )

    if not image_url:
        print(
            f"TVmaze: matched {title}, but no poster exists",
            flush=True,
        )
        return None

    try:
        image_response = requests.get(
            image_url,
            headers={
                "User-Agent": USER_AGENT,
            },
            timeout=30,
        )

        image_response.raise_for_status()
    except Exception as error:
        print(
            f"TVmaze image download failed for "
            f"{title}: {error}",
            flush=True,
        )
        return None

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
    }.get(content_type, ".jpg")

    destination = (
        PROGRAM_ARTWORK_DIR
        / f"{safe_id}{extension}"
    )

    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    temporary.write_bytes(
        image_response.content
    )

    temporary.replace(destination)

    print(
        f"TVmaze artwork cached: "
        f"{title} -> {destination.name}",
        flush=True,
    )

    return destination.name
