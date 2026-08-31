#!/usr/bin/env python3
"""Install Schedules Direct program-artwork support into SignalDVR.

Run from the SignalDVR repository root:
    python3 install_program_artwork.py
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
APP_PY = ROOT / "app" / "app.py"
SD_PY = ROOT / "app" / "schedules_direct.py"

for path in (APP_PY, SD_PY):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run this script from ~/signaldvr")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for path in (APP_PY, SD_PY):
    backup = path.with_name(path.name + f".before-program-artwork-{stamp}")
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

sd_text = SD_PY.read_text(encoding="utf-8")

if "PROGRAM_ARTWORK_DIR =" not in sd_text:
    marker = 'BASE_URL = "https://json.schedulesdirect.org/20141201"\n'
    addition = '''BASE_URL = "https://json.schedulesdirect.org/20141201"\n\n# Cached Schedules Direct program images served by Flask.\nPROGRAM_ARTWORK_DIR = Path(__file__).resolve().parent / "static" / "program_artwork"\nPROGRAM_ARTWORK_DIR.mkdir(parents=True, exist_ok=True)\n'''
    if marker not in sd_text:
        raise SystemExit("Could not find BASE_URL marker in app/schedules_direct.py")
    sd_text = sd_text.replace(marker, addition, 1)

if "def cache_program_artwork(" not in sd_text:
    sd_text += r'''


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
    """Download and cache the best Schedules Direct image for a program.

    Returns the cached filename, or None when no usable image is available.
    """
    safe_id = _safe_program_id(program_id)
    if not safe_id:
        return None

    # Reuse any previously downloaded image regardless of extension.
    cached = sorted(PROGRAM_ARTWORK_DIR.glob(f"{safe_id}.*"))
    if cached:
        return cached[0].name

    token = get_token()
    response = requests.post(
        f"{BASE_URL}/metadata/programs",
        headers={"token": token, "Accept": "application/json"},
        json=[safe_id],
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, list) or not payload:
        return None

    entry = next(
        (
            candidate
            for candidate in payload
            if str(candidate.get("programID") or "") == safe_id
        ),
        payload[0],
    )
    images = entry.get("data")
    if not isinstance(images, list) or not images:
        return None

    candidates = [image for image in images if image.get("uri")]
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

    content_type = image_response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    extension = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type)

    if extension is None:
        suffix = Path(image_url.split("?", 1)[0]).suffix.lower()
        extension = suffix if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"

    destination = PROGRAM_ARTWORK_DIR / f"{safe_id}{extension}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(image_response.content)
    temporary.replace(destination)
    return destination.name
'''

SD_PY.write_text(sd_text, encoding="utf-8")

app_text = APP_PY.read_text(encoding="utf-8")
old_route = '''@app.route("/api/kodi/nowplaying/<channel>")
def api_kodi_nowplaying(channel):
    ch = database.get_channel(channel)

    programs = database.get_programs_for_channel(channel, limit=2)
    rows = [dict(p) for p in programs]

    return jsonify({
        "channel": channel,
        "name": ch["guide_name"] if ch else channel,
        "current": rows[0] if len(rows) > 0 else None,
        "next": rows[1] if len(rows) > 1 else None
    })
'''
new_route = '''def _program_with_artwork_url(row):
    if not row:
        return None

    item = dict(row)
    artwork_flag = str(item.get("artwork") or "").strip().lower()
    program_id = str(item.get("programid") or "").strip()

    if program_id and artwork_flag in ("true", "1", "yes"):
        item["artwork"] = (
            request.host_url.rstrip("/")
            + "/api/program-artwork/"
            + quote(program_id, safe="")
        )
    elif artwork_flag.startswith(("http://", "https://", "/")):
        # Preserve a real URL/path if a future importer stores one directly.
        item["artwork"] = item.get("artwork")
    else:
        item["artwork"] = None

    return item


@app.route("/api/program-artwork/<program_id>")
def api_program_artwork(program_id):
    from flask import abort

    try:
        filename = schedules_direct.cache_program_artwork(program_id)
    except Exception as error:
        print(
            f"Program artwork lookup failed for {program_id}: {error}",
            flush=True,
        )
        abort(404)

    if not filename:
        abort(404)

    return send_from_directory(
        schedules_direct.PROGRAM_ARTWORK_DIR,
        filename,
        as_attachment=False,
        max_age=86400,
    )


@app.route("/api/kodi/nowplaying/<channel>")
def api_kodi_nowplaying(channel):
    ch = database.get_channel(channel)

    programs = database.get_programs_for_channel(channel, limit=2)
    rows = [_program_with_artwork_url(p) for p in programs]

    return jsonify({
        "channel": channel,
        "name": ch["guide_name"] if ch else channel,
        "current": rows[0] if len(rows) > 0 else None,
        "next": rows[1] if len(rows) > 1 else None
    })
'''

if "def api_program_artwork(" not in app_text:
    if old_route not in app_text:
        raise SystemExit("Could not find the current /api/kodi/nowplaying route in app/app.py")
    app_text = app_text.replace(old_route, new_route, 1)

APP_PY.write_text(app_text, encoding="utf-8")

print("\nInstalled program artwork support.")
print("Run:")
print("  python3 -m py_compile app/app.py app/schedules_direct.py")
print("  sudo systemctl restart signaldvr")
