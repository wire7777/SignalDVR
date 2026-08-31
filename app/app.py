from flask import Flask, send_from_directory, send_file, after_this_request, redirect, render_template, jsonify, request
import json
import gzip
import time
import datetime
import sqlite3
from app import playback_index
from pathlib import Path
import subprocess
import threading
from app import config
from app import database
from app import epg
from app import hdhr
from app import native_dvb
from app import search
from app import stream_manager
from app.stream_engine import playback
from app import thumbnails
from app import timeshift
from app import tuner_manager
from app import scheduler_service
from app import guide_service
from app import cleanup_service
from app.recorder import Recorder
from app import schedules_direct
from app import tvmaze_artwork
from app import live_buffer
from app import live_segments
from app import background_dvr
from app import program_catalog
from app.stream_engine import seek
from app.stream_engine.web import web_stream_manager
from app.stream_engine import playlist
from app.stream_engine import vod
from app.stream_engine import recorder as stream_recorder
from app.stream_engine import manager as stream_engine
from app.stream_engine.playback_manager import manager as playback_manager
from app import library
import shutil
import os
import mimetypes
from app import artwork_service
from app import resume_manager
from app import recording_finalize
from app import setup_service
from app import download_service
from app import catalog_integrity
from urllib.parse import quote





mimetypes.add_type(
    "application/vnd.apple.mpegurl",
    ".m3u8",
    strict=True,
)
mimetypes.add_type(
    "video/mp2t",
    ".ts",
    strict=True,
)

app = Flask(__name__)
recorder = Recorder()


def _restart_signaldvr_service_delayed(delay_seconds=2.0):
    """
    Restart SignalDVR after the current settings response has time to return.

    The delayed restart prevents the browser request from being interrupted
    before Flask sends the redirect response.
    """
    def restart_service():
        if os.environ.get("SIGNALDVR_CONTAINER", "").strip() == "1":
            print("SignalDVR container restart requested.", flush=True)
            os._exit(0)

        try:
            result = subprocess.run(
                [
                    "/bin/systemctl",
                    "--no-block",
                    "restart",
                    "signaldvr.service",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                print(
                    "SignalDVR automatic restart requested.",
                    flush=True,
                )
            else:
                print(
                    "SignalDVR automatic restart failed: "
                    f"returncode={result.returncode} "
                    f"error={result.stderr.strip()}",
                    flush=True,
                )

        except Exception as error:
            print(
                f"Could not restart SignalDVR automatically: {error}",
                flush=True,
            )

    timer = threading.Timer(delay_seconds, restart_service)
    timer.daemon = True
    timer.start()


def _migrate_legacy_media_assets():
    """Move old source-tree artwork/logos into configured media storage."""
    legacy_static = Path(__file__).resolve().parent / "static"
    migrations = (
        (legacy_static / "program_artwork", Path(config.THUMBNAILS) / "program_artwork"),
        (legacy_static / "logos", Path(config.THUMBNAILS) / "logos"),
    )

    for source_dir, destination_dir in migrations:
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)

            if not source_dir.exists():
                continue

            for source_file in source_dir.iterdir():
                if not source_file.is_file():
                    continue

                destination_file = destination_dir / source_file.name

                # Keep the external copy if one already exists. Otherwise move
                # the legacy file out of the application/development tree.
                if destination_file.exists():
                    source_file.unlink()
                else:
                    shutil.move(str(source_file), str(destination_file))

            try:
                source_dir.rmdir()
            except OSError:
                pass

        except Exception as error:
            print(
                f"Asset migration failed for {source_dir}: {error}",
                flush=True,
            )


_migrate_legacy_media_assets()


@app.route("/static/logos/<path:filename>")
def external_channel_logo(filename):
    return send_from_directory(
        Path(config.THUMBNAILS) / "logos",
        filename,
        as_attachment=False,
        max_age=86400,
    )


def live_program_payload(segment):
    if not segment:
        return None

    return {
        "segment_id": segment.get("id"),
        "program_id": segment.get("program_id"),
        "title": segment.get("title"),
        "subtitle": segment.get("subtitle"),
        "description": segment.get("description"),
        "category": segment.get("category"),
        "episode": segment.get("episode"),
        "start": segment.get("start_time"),
        "stop": segment.get("stop_time"),
        "channel": segment.get("channel"),
        "channel_name": segment.get("guide_name"),
        "status": segment.get("status"),
        "saved": bool(segment.get("saved")),
        "auto_expire": bool(segment.get("auto_expire")),
    }


@app.template_filter("category_color")
def category_color(category):
    colors = {
        "Sports": "#1b5e20",
        "News": "#0d47a1",
        "Movie": "#4a148c",
        "Movies": "#4a148c",
        "Comedy": "#795548",
        "Kids": "#ef6c00",
        "Children": "#ef6c00",
        "Drama": "#b71c1c",
        "Reality": "#00695c",
        "Documentary": "#5d4037",
        "Music": "#ad1457",
    }
    return colors.get(category or "", "#263238")


@app.template_filter("tvtime")
def tvtime(value):
    if not value:
        return ""

    try:
        from datetime import datetime
        raw = str(value)[:14]
        dt = datetime.strptime(raw, "%Y%m%d%H%M%S")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value)


@app.template_filter("progress_percent")
def progress_percent(start, stop):
    try:
        from datetime import datetime

        now = datetime.now()
        s = datetime.strptime(str(start)[:14], "%Y%m%d%H%M%S")
        e = datetime.strptime(str(stop)[:14], "%Y%m%d%H%M%S")

        if now <= s:
            return 0
        if now >= e:
            return 100

        total = (e - s).total_seconds()
        elapsed = (now - s).total_seconds()

        return int((elapsed / total) * 100)
    except Exception:
        return 0


@app.template_filter("is_live_now")
def is_live_now(start, stop):
    try:
        from datetime import datetime

        now = datetime.now()
        s = datetime.strptime(str(start)[:14], "%Y%m%d%H%M%S")
        e = datetime.strptime(str(stop)[:14], "%Y%m%d%H%M%S")

        return s <= now < e
    except Exception:
        return False


@app.template_filter("logo_file")
def logo_file(value):
    if not value:
        return ""

    safe = str(value).lower()
    safe = safe.replace(" ", "")
    safe = safe.replace("-", "")
    safe = safe.replace(".", "")

    return safe + ".png"
@app.template_filter("logo_name")
def logo_name(value):
    if not value:
        return ""

    safe = str(value).lower()
    for ch in [" ", "-", ".", "_"]:
        safe = safe.replace(ch, "")

    return safe + ".png"

@app.template_filter("status_badge")
def status_badge(status):
    status = status or ""

    if status == "Recording":
        return "🔴 Recording"
    if status == "Recorded":
        return "✅ Recorded"
    if status == "Scheduled":
        return "🕒 Scheduled"
    if status == "Expired":
        return "⌛ Expired"
    if status.startswith("Failed"):
        return "❌ " + status

    return status


@app.template_filter("has_hd")
def has_hd(value):
    return "HD" in (value or "") or "hdtv" in (value or "").lower()


@app.template_filter("has_dd51")
def has_dd51(value):
    return "DD 5.1" in (value or "")


@app.template_filter("short_video")
def short_video(value):
    value = value or ""

    if "1080i" in value:
        return "1080i"
    if "720p" in value:
        return "720p"
    if "480i" in value:
        return "480i"

    return ""

for folder in [
    config.RECORDINGS,
    config.LOGS,
    config.LIVEBUFFER,
    config.THUMBNAILS,
    config.GUIDE,
]:
    folder.mkdir(parents=True, exist_ok=True)


database.init_db()


@app.before_request
def require_first_run_setup():
    if setup_service.is_complete():
        return None

    allowed = (
        request.path.startswith("/setup")
        or request.path.startswith("/static/setup/")
        or request.path == "/api/setup/status"
    )
    if allowed:
        return None

    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "setup_required": True,
            "setup_url": "/setup",
        }), 503

    return redirect("/setup")


@app.route("/api/setup/status")
def api_setup_status():
    complete = setup_service.is_complete()
    return jsonify({
        "ok": True,
        "setup_complete": complete,
        "setup_required": not complete,
    })


@app.route("/setup", methods=["GET", "POST"])
def first_run_setup():
    if setup_service.is_complete():
        return redirect("/")

    error = None
    if request.method == "POST":
        if request.form.get("password") != request.form.get("confirm_password"):
            error = "Passwords do not match."
        else:
            try:
                setup_service.finish(request.form)
                _restart_signaldvr_service_delayed(1.0)
                return redirect("/")
            except Exception as exc:
                error = str(exc)

    tuners, tuner_error = setup_service.discover_hdhr_devices()
    return render_template(
        "setup/index.html",
        storage=setup_service.storage_status(),
        tuners=tuners,
        tuner_error=tuner_error,
        error=error,
    )


@app.route("/")
def index():
    recordings = database.list_recordings()
    channels = database.get_now_next()
    status = "RECORDING" if recorder.is_recording() else "IDLE"
    return render_template("index.html", recordings=recordings, channels=channels, status=status)


@app.route("/stop", methods=["POST"])
def stop():
    recorder.stop()
    return redirect("/")


@app.route("/recording/details/<path:filename>")
def recording_details(filename):
    recording = None

    for r in database.list_recordings():
        if r["filename"] == filename:
            recording = dict(r)
            break

    if not recording:
        return "Recording not found", 404

    program = database.get_program_by_id(recording.get("programid", ""))

    if program:
     recording.update(dict(program))

    recording["thumbnail"] = thumbnails.make_thumbnail(recording["filename"])

    return render_template("recording_details.html", r=recording)

@app.route("/record-channel/<guide_number>", methods=["POST"])
def record_channel(guide_number):
    ch = database.get_channel(guide_number)
    if ch:
        recorder.start_from_channel(ch)
    return redirect("/")


@app.route("/play/<path:filename>")
def play(filename):
    response = send_from_directory(
        config.RECORDINGS,
        filename,
        as_attachment=False,
        conditional=True,
    )

    lower_name = filename.lower()

    if lower_name.endswith(".m3u8"):
        response.mimetype = "application/vnd.apple.mpegurl"
    elif lower_name.endswith(".ts"):
        response.mimetype = "video/mp2t"

    return response


@app.route("/recording/play/<path:filename>")
def recording_player(filename):
    """Legacy filename route: send web playback through the HLS VOD player."""
    for row in database.list_recordings():
        if row["filename"] == filename:
            return redirect(f"/recording/player/{int(row['id'])}")

    return "Recording not found", 404


@app.route("/delete/<path:filename>", methods=["GET", "POST"])
def delete_recording(filename):
    path = config.RECORDINGS / filename

    print("Delete requested:", filename, flush=True)
    print("Delete path:", path, flush=True)
    print("Exists:", path.exists(), flush=True)

    if path.exists():
        path.unlink()

    stem = path.stem
    for thumb in config.THUMBNAILS.glob(stem + ".*"):
        try:
            thumb.unlink()
        except Exception as e:
            print("Thumbnail delete error:", e, flush=True)

    database.delete_recording(filename)
    return redirect("/recordings")


@app.route("/scan-channels", methods=["POST"])
def scan_channels():
    """
    Legacy web action.

    HDHomeRun lineup import remains available here. Native DVB has its own
    hardware-aware scan API at /api/native-dvb/scan.
    """
    results = {}

    if database.get_setting("hdhomerun_enabled", "1") == "1":
        try:
            results["hdhomerun"] = hdhr.import_lineup()
        except Exception as error:
            results["hdhomerun_error"] = str(error)

    print("Channel scan:", results, flush=True)
    return redirect("/")


@app.route("/scan-channels/hdhomerun", methods=["POST"])
def scan_channels_hdhomerun():
    try:
        count = hdhr.import_lineup()
        return jsonify({
            "ok": True,
            "imported": count,
        })
    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


# ---------------------------------------------------------------------
# Native DVB discovery / scan
# ---------------------------------------------------------------------

_native_dvb_scan_lock = threading.Lock()

_native_dvb_scan_state = {
    "running": False,
    "stage": "idle",
    "message": "",
    "error": "",
    "adapter": None,
    "frequency_count": 0,
    "channel_count": 0,
    "map_path": "",
    "frequency_path": "",
}


def _native_dvb_scan_snapshot():
    with _native_dvb_scan_lock:
        state = dict(_native_dvb_scan_state)

    try:
        adapters = native_dvb.discover_adapters()
    except Exception:
        adapters = []

    try:
        mapped_channels = len(
            native_dvb.load_map()
        )
    except Exception:
        mapped_channels = 0

    state.update({
        "ok": True,
        "adapter_count": len(adapters),
        "adapters": adapters,
        "mapped_channels": mapped_channels,
        "default_map": str(
            native_dvb.DEFAULT_MAP
        ),
        "frequency_file": str(
            native_dvb.DEFAULT_VDR_SCAN
        ),
    })

    return state


def _run_native_dvb_scan(country):
    try:
        with _native_dvb_scan_lock:
            _native_dvb_scan_state.update({
                "running": True,
                "stage": "scanning",
                "message": (
                    "Scanning PCIe / Native DVB channels..."
                ),
                "error": "",
                "adapter": None,
                "frequency_count": 0,
                "channel_count": 0,
            })

        result = native_dvb.scan_native_dvb(
            adapter=None,
            country=country,
        )

        with _native_dvb_scan_lock:
            _native_dvb_scan_state.update({
                "running": False,
                "stage": "complete",
                "message": (
                    f"Native DVB scan complete: "
                    f"{result.get('channel_count', 0)} "
                    f"channels found."
                ),
                "error": "",
                "adapter": result.get("adapter"),
                "frequency_count": result.get(
                    "frequency_count",
                    0,
                ),
                "channel_count": result.get(
                    "channel_count",
                    0,
                ),
                "map_path": result.get(
                    "map_path",
                    "",
                ),
                "frequency_path": result.get(
                    "frequency_path",
                    "",
                ),
            })

        print(
            "Native DVB web scan complete:",
            result,
            flush=True,
        )

    except Exception as error:
        print(
            "Native DVB web scan error:",
            error,
            flush=True,
        )

        with _native_dvb_scan_lock:
            _native_dvb_scan_state.update({
                "running": False,
                "stage": "error",
                "message": "Native DVB scan failed.",
                "error": str(error),
            })


@app.route(
    "/api/native-dvb/status",
    methods=["GET"],
)
def api_native_dvb_status():
    return jsonify(
        _native_dvb_scan_snapshot()
    )


@app.route(
    "/api/native-dvb/scan",
    methods=["POST"],
)
def api_native_dvb_scan():
    body = request.get_json(
        silent=True
    ) or {}

    country = str(
        body.get("country")
        or "US"
    ).strip().upper()

    if not native_dvb.discover_adapters():
        return jsonify({
            "ok": False,
            "error": (
                "No PCIe / Native DVB hardware detected"
            ),
        }), 404

    with _native_dvb_scan_lock:
        if _native_dvb_scan_state.get("running"):
            return jsonify({
                "ok": False,
                "error": (
                    "Native DVB scan is already running"
                ),
            }), 409

        _native_dvb_scan_state.update({
            "running": True,
            "stage": "starting",
            "message": (
                "Starting PCIe / Native DVB scan..."
            ),
            "error": "",
        })

    thread = threading.Thread(
        target=_run_native_dvb_scan,
        args=(country,),
        name="signaldvr-native-dvb-scan",
        daemon=True,
    )

    thread.start()

    return jsonify({
        "ok": True,
        "started": True,
        "country": country,
    })


@app.route("/api/tuners/channel-sources")
def api_tuner_channel_sources():
    rows = database.list_channels()
    result = []

    for row in rows:
        guide_number = str(
            row["guide_number"]
        )

        has_hdhr = bool(
            str(row["url"] or "").strip()
        )

        try:
            has_native_dvb = (
                native_dvb.get_service(
                    guide_number
                )
                is not None
            )
        except Exception:
            has_native_dvb = False

        result.append({
            "guide_number": guide_number,
            "guide_name": row["guide_name"],
            "has_hdhomerun": has_hdhr,
            "has_native_dvb": has_native_dvb,
            "resolved_source_now": (
                database.resolve_channel_source(row)
            ),
        })

    return jsonify(
        sorted(
            result,
            key=lambda channel: channel[
                "guide_number"
            ],
        )
    )


@app.route("/api/background-dvr/status", methods=["GET"])
def api_background_dvr_status():
    try:
        return jsonify(background_dvr.status())
    except Exception as error:
        print(
            f"Background DVR status error: {error}",
            flush=True,
        )
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


@app.route("/api/catalog/health", methods=["GET"])
def api_catalog_health():
    try:
        return jsonify(catalog_integrity.verify())
    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


@app.route("/api/catalog/rebuild", methods=["POST"])
def api_catalog_rebuild():
    try:
        return jsonify(catalog_integrity.repair_missing())
    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500

def _background_dvr_usage_payload():
    settings = database.get_background_dvr_settings()
    programs_root = Path(config.LIVEBUFFER) / "programs"
    used_bytes = 0

    if programs_root.exists():
        for path in programs_root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    used_bytes += path.stat().st_size
            except (FileNotFoundError, PermissionError, OSError):
                continue

    limit_bytes = int(settings["storage_limit_gb"]) * 1024 * 1024 * 1024
    percent_used = round((used_bytes / limit_bytes) * 100, 1) if limit_bytes > 0 else 0.0

    return {
        **settings,
        "used_bytes": used_bytes,
        "used_gb": round(used_bytes / (1024 ** 3), 2),
        "limit_bytes": limit_bytes,
        "percent_used": percent_used,
    }


@app.route("/api/background-dvr/settings", methods=["GET", "POST"])
def api_background_dvr_settings():
    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form.to_dict()
        allowed = {
            "enabled": "background_dvr_enabled",
            "retention": "background_dvr_retention",
            "storage_limit_gb": "background_dvr_storage_limit_gb",
            "when_full": "background_dvr_when_full",
            "keep_saved": "background_dvr_keep_saved",
            "show_library": "background_dvr_show_library",
        }

        current = database.get_background_dvr_settings()
        retention = str(payload.get("retention", current["retention"])).strip().lower()
        if retention not in {"end_of_day", "1_day", "3_days", "7_days", "14_days", "30_days", "never"}:
            return jsonify({"ok": False, "error": "Invalid retention value"}), 400

        when_full = str(payload.get("when_full", current["when_full"])).strip().lower()
        if when_full not in {"delete_oldest", "stop_buffering"}:
            return jsonify({"ok": False, "error": "Invalid when_full value"}), 400

        try:
            storage_limit_gb = max(0, int(float(payload.get("storage_limit_gb", current["storage_limit_gb"]))))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "storage_limit_gb must be a number"}), 400

        def api_bool(value):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        normalized = {
            "enabled": "1" if api_bool(payload.get("enabled", current["enabled"])) else "0",
            "retention": retention,
            "storage_limit_gb": str(storage_limit_gb),
            "when_full": when_full,
            "keep_saved": "1" if api_bool(payload.get("keep_saved", current["keep_saved"])) else "0",
            "show_library": "1" if api_bool(payload.get("show_library", current["show_library"])) else "0",
        }

        for public_key, setting_key in allowed.items():
            database.set_setting(setting_key, normalized[public_key])

    return jsonify({"ok": True, **_background_dvr_usage_payload()})


@app.route("/api/recordings/<int:recording_id>/playback-index")
def api_recording_playback_index(recording_id):
    try:
        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        if not filename:
            return jsonify({
                "ok": False,
                "error": "Recording has no filename",
            }), 400

        index_data = playback_index.get_or_build_index(filename)

        return jsonify({
            "ok": True,
            "recording_id": recording_id,
            "filename": filename,
            "title": recording.get("title") or filename,
            "duration_seconds": index_data.get("duration_seconds", 0),
            "source_start_time": index_data.get("source_start_time", 0),
            "interval_seconds": index_data.get("interval_seconds", 2),
            "seek_point_count": index_data.get("seek_point_count", 0),
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/seek")
def api_recording_seek(recording_id):
    try:
        target_seconds = float(
            request.args.get("seconds", "0") or 0
        )

        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        if not filename:
            return jsonify({
                "ok": False,
                "error": "Recording has no filename",
            }), 400

        seek_point = playback_index.find_seek_point(
            filename,
            target_seconds,
        )

        return jsonify({
            "ok": True,
            "recording_id": recording_id,
            "filename": filename,
            **seek_point,
        })

    except ValueError:
        return jsonify({
            "ok": False,
            "error": "seconds must be a number",
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

@app.route("/import-epg", methods=["POST"])
def import_epg():
    epg.update_guide()
    database.apply_series_rules()
    return redirect("/")


@app.route("/guide")
def guide_page():
    channels = database.get_now_next()
    return render_template("guide.html", channels=channels)


@app.route("/guide-view/<channel>")
def guide_view_channel(channel):
    programs = database.get_programs_for_channel(channel, limit=30)
    ch = database.get_channel(channel)

    return render_template(
        "channel_guide.html",
        channel=channel,
        guide_name=ch["guide_name"] if ch else "",
        programs=programs,
    )

@app.route("/guide/<channel>")
def guide_channel_json(channel):
    return jsonify(database.get_programs_for_channel(channel, limit=30))


@app.route("/grid")
def grid_page():
    grid = database.get_guide_grid(limit_channels=100, limit_programs=12)
    return render_template("grid.html", grid=grid)


@app.route("/scheduled")
def scheduled_page():
    scheduled = database.list_upcoming_scheduled_recordings()
    return render_template("scheduled.html", scheduled=scheduled)


@app.route("/schedule-program", methods=["POST"])
def schedule_program():
    database.add_scheduled_recording(
        channel=request.form.get("channel", ""),
        title=request.form.get("title", ""),
        subtitle=request.form.get("subtitle", ""),
        start=request.form.get("start", ""),
        stop=request.form.get("stop", ""),
    )
    return redirect("/scheduled")


@app.route("/scheduled/delete/<int:schedule_id>", methods=["POST"])
def delete_scheduled(schedule_id):
    database.delete_scheduled_recording(schedule_id)
    return redirect("/scheduled")


@app.route("/series")
def series_page():
    series = database.list_series_recordings()
    return render_template("series.html", series=series)


@app.route("/series/add", methods=["POST"])
def add_series():
    database.add_series_recording(
        title=request.form.get("title", ""),
        channel=request.form.get("channel", ""),
        only_new=0,
        priority=100,
    )

    database.apply_series_rules()
    return redirect("/series")


@app.route("/series/apply", methods=["POST"])
def apply_series():
    database.apply_series_rules()
    return redirect("/scheduled")


@app.route("/series/priority/<int:series_id>/<int:priority>", methods=["POST"])
def set_series_priority(series_id, priority):
    database.set_series_priority(series_id, priority)
    return redirect("/series")



@app.route("/series/update/<int:series_id>", methods=["POST"])
def update_series(series_id):
    database.update_series_recording(
        series_id=series_id,
        title=request.form.get("title", ""),
        channel=request.form.get("channel", ""),
        only_new=1 if request.form.get("only_new") == "on" else 0,
        enabled=1 if request.form.get("enabled") == "on" else 0,
        priority=int(request.form.get("priority", 50)),
        start_padding=int(request.form.get("start_padding", 2)),
        end_padding=int(request.form.get("end_padding", 5)),
        keep_last=int(request.form.get("keep_last", 0)),
        any_channel=1 if request.form.get("any_channel") == "on" else 0,
    )

    database.apply_series_rules()
    return redirect("/series")

@app.route("/series/delete/<int:series_id>", methods=["POST"])
def delete_series(series_id):
    database.delete_series_recording(series_id)
    return redirect("/series")


@app.route("/conflicts")
def conflicts_page():
    conflicts = database.get_schedule_conflicts()
    return render_template("conflicts.html", conflicts=conflicts)


@app.route("/history")
def history_page():
    history = database.list_recording_history()
    return render_template("history.html", history=history)


@app.route("/history/clear", methods=["POST"])
@app.route("/history/clear-all", methods=["POST"])
def clear_history():
    database.clear_all_recording_history()
    return redirect("/history")


@app.route("/history/clear-completed", methods=["POST"])
def clear_completed_history():
    database.clear_completed_recording_history()
    return redirect("/history")


@app.route("/search")
def search_page():
    q = request.args.get("q", "").strip()
    results = search.search_programs(q) if q else []
    return render_template("search.html", q=q, results=results)


@app.route("/recordings")
def recordings_page():
    q = request.args.get("q", "").strip().lower()
    sort = request.args.get("sort", "date")

    recordings = []

    for r in database.list_recordings():
        item = dict(r)
        item["thumbnail"] = thumbnails.make_thumbnail(item["filename"])

        if q:
            haystack = " ".join([
                str(item.get("title", "")),
                str(item.get("subtitle", "")),
                str(item.get("channel", "")),
                str(item.get("filename", "")),
            ]).lower()

            if q not in haystack:
                continue

        recordings.append(item)

    if sort == "title":
        recordings.sort(key=lambda x: (x.get("title") or "").lower())
    elif sort == "channel":
        recordings.sort(key=lambda x: str(x.get("channel") or ""))
    elif sort == "size":
        recordings.sort(key=lambda x: int(x.get("size_bytes") or 0), reverse=True)
    else:
        recordings.sort(key=lambda x: str(x.get("start_time") or ""), reverse=True)

    return render_template(
        "recordings.html",
        recordings=recordings,
        q=q,
        sort=sort,
    )

@app.route("/thumbs/<path:filename>")
def thumbs(filename):
    return send_from_directory(config.THUMBNAILS, filename, as_attachment=False)


@app.route("/timeshift")
def timeshift_page():
    channels = database.list_channels()
    return render_template(
        "timeshift.html",
        channels=channels,
        status=timeshift.status(),
    )

@app.route("/api/stream/watch_from_beginning")
def api_watch_from_beginning():

    active = stream_engine.get_active()

    if not active:
        return jsonify({
            "ok": False,
            "error": "No active stream"
        })

    result = playback.watch_from_beginning(
        active["session_dir"]
    )

    if not result:
        return jsonify({
            "ok": False
        })

    result["ok"] = True
    return jsonify(result)


@app.route("/api/stream/playback")
def api_stream_playback():
    active = stream_engine.get_active()

    if not active:
        return jsonify({
            "ok": False,
            "error": "No active stream"
        })

    from app.stream_engine import index, metadata

    index.rebuild_index_from_files(active["session_dir"])
    metadata.attach_program(active["session_dir"], active["channel"])

    return jsonify(
        playback.playback_status(
            active["session_dir"]
        )
    )
    
@app.route("/api/stream/seek")
def api_stream_seek():
    try:
        seconds = int(request.args.get("seconds", "-30"))

        active = stream_engine.get_active()

        if not active:
            return jsonify({
                "ok": False,
                "error": "No active stream"
            }), 404

        result = seek.build_seek_playlist(
            active["session_dir"],
            seconds=seconds,
            output_name="seek.m3u8",
        )

        base = request.host_url.rstrip("/")

        return jsonify({
            "ok": True,
            "seconds": seconds,
            "segment": result["segment"],
            "playlist": result["playlist"],
            "playlist_url": f"{base}/livebuffer/sessions/{active['session_id']}/{result['playlist']}"
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/playback/status")
def api_playback_manager_status():
    active = stream_engine.get_active()

    if not active:
        return jsonify({
            "ok": False,
            "error": "No active stream"
        }), 404

    live_segment = live_segments.sync_active_segment(active)
    status = playback.playback_status(active["session_dir"])

    current_program = live_program_payload(live_segment)

    return jsonify({
        "ok": True,
        "active": active,
        "playback": status,
        "live_program_segment": live_segment,
        "current_program": current_program,
    })


@app.route("/api/playback/live")
def api_playback_manager_live():
    result = playback_manager.jump_live()

    if not result:
        return jsonify({
            "ok": False,
            "error": "No active stream"
        }), 404

    active = result.get("active") or stream_engine.get_active()
    live_segment = result.get("live_program_segment")
    current_program = live_program_payload(live_segment)
    base = request.host_url.rstrip("/")

    return jsonify({
        "ok": True,
        "live": True,
        "playlist_url": base + result["playlist"],
        "active": active,
        "live_program_segment": live_segment,
        "current_program": current_program,
    })


@app.route("/api/playback/seek")
def api_playback_manager_seek():
    try:
        # Relative intent: positive = fast-forward, negative = rewind.
        seconds = int(request.args.get("seconds", "-10"))
        seconds = max(-3600, min(3600, seconds))

        result = playback_manager.seek(seconds)

        if not result:
            return jsonify({
                "ok": False,
                "error": "No active stream"
            }), 404

        active = result.get("active") or stream_engine.get_active()
        base = request.host_url.rstrip("/")
        playlist = result["playlist"]

        if playlist.startswith("/"):
            playlist_url = base + playlist
        else:
            playlist_url = (
                f"{base}/livebuffer/sessions/"
                f"{active['session_id']}/{playlist}"
            )

        return jsonify({
            "ok": True,
            "live": bool(result.get("live")),
            "mode": result.get("mode"),
            "seconds": seconds,
            "seconds_behind": int(result.get("seconds_behind", 0)),
            "segment": result.get("segment"),
            "playlist": playlist,
            "playlist_url": playlist_url,
        })

    except ValueError:
        return jsonify({
            "ok": False,
            "error": "seconds must be an integer"
        }), 400
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500



@app.route("/api/playback/delayed-live")
def api_playback_delayed_live():
    try:
        seconds = int(request.args.get("seconds", "30"))
        seconds = max(4, abs(seconds))

        result = playback_manager.start_delayed_live(
            seconds_behind=seconds
        )

        if not result:
            return jsonify({
                "ok": False,
                "error": "No active stream"
            }), 404

        active = result["active"]
        base = request.host_url.rstrip("/")

        return jsonify({
            "ok": True,
            "live": False,
            "mode": "delayed_live",
            "seconds_behind": result["seconds_behind"],
            "playlist": result["playlist"],
            "playlist_url": (
                f"{base}/livebuffer/sessions/"
                f"{active['session_id']}/"
                f"{result['playlist']}"
            )
        })

    except ValueError:
        return jsonify({
            "ok": False,
            "error": "seconds must be a number"
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/playback/delayed-live/status")
def api_playback_delayed_live_status():
    result = playback_manager.delayed_live_status()

    if not result:
        return jsonify({
            "ok": False,
            "error": "No active stream"
        }), 404

    return jsonify({
        "ok": True,
        **result,
    })


@app.route("/api/timeshift/status")
def api_timeshift_status():
    return jsonify(timeshift.status())


@app.route("/api/timeshift/start/<channel>", methods=["POST"])
def api_timeshift_start(channel):
    ok = timeshift.start(channel)
    return jsonify({"ok": ok, "status": timeshift.status()})


@app.route("/api/timeshift/stop", methods=["POST"])
def api_timeshift_stop():
    timeshift.stop()
    return jsonify({"ok": True})


@app.route("/api/timeshift/pause", methods=["POST"])
def api_timeshift_pause():
    timeshift.pause()
    return jsonify(timeshift.status())


@app.route("/api/timeshift/resume", methods=["POST"])
def api_timeshift_resume():
    timeshift.resume()
    return jsonify(timeshift.status())


@app.route("/api/timeshift/replay/<int:seconds>", methods=["POST"])
def api_timeshift_replay(seconds):
    timeshift.seek_relative(-seconds)
    return jsonify(timeshift.status())


@app.route("/api/timeshift/skip/<int:seconds>", methods=["POST"])
def api_timeshift_skip(seconds):
    timeshift.seek_relative(seconds)
    return jsonify(timeshift.status())


@app.route("/api/timeshift/seek/<int:seconds>", methods=["POST"])
def api_timeshift_seek(seconds):
    timeshift.seek_relative(seconds)
    return jsonify(timeshift.status())


@app.route("/api/timeshift/live", methods=["POST"])
def api_timeshift_live():
    timeshift.jump_to_live()
    return jsonify(timeshift.status())


@app.route("/api/timeshift/now")
def api_timeshift_now():
    import datetime

    status = timeshift.status()
    channel_label = status.get("channel", "")
    channel = channel_label.split(" ")[0] if channel_label else ""

    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    program = database.get_current_program(channel, now)

    return jsonify({
        "channel": channel_label,
        "program": program,
    })


@app.route("/timeshiftbuffer/<path:filename>")
def timeshiftbuffer(filename):
    return send_from_directory(config.LIVEBUFFER, filename, as_attachment=False)


@app.route("/live")
def live_page():
    channels = database.list_channels()
    current = stream_manager.current_channel()
    live_status = stream_manager.status()

    return render_template(
        "live.html",
        channels=channels,
        current=current,
        live_status=live_status,
    )


@app.route("/api/live/start/<channel>", methods=["POST"])
def api_live_start(channel):
    """
    Compatibility route backed by Playback Engine v2.
    """
    try:
        # Stop any leftover legacy root-level stream.
        stream_manager.stop_stream()

        active = stream_engine.start_or_reuse(channel)

        return jsonify({
            "ok": True,
            "channel": active.get("channel"),
            "guide_name": active.get("guide_name"),
            "session_id": active.get("session_id"),
            "playlist_url": active.get("playlist_url"),
            "active": active,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/live/stop", methods=["POST"])
def live_stop():
    stream_manager.stop_stream()
    stream_engine.stop_active()

    return jsonify({
        "ok": True,
        "active": None,
    })



# -----------------------------------------------------------------------------
# Roku shared playback bridge
# -----------------------------------------------------------------------------
# Roku televisions do not decode the MPEG-2 video carried by the native OTA
# Playback Engine v2 HLS session.  Keep the real SignalDVR playback session as
# the source of truth, then transcode whichever live/seek playlist that session
# selects into a small, separate H.264/AAC HLS output for Roku.
_roku_playback_lock = threading.RLock()
_roku_playback_process = None
_roku_playback_log_handle = None
_roku_playback_state = {
    "channel": "",
    "session_id": "",
    "mode": "stopped",
    "source_playlist": "",
    "output_dir": "",
}


def _roku_output_dir():
    return Path(config.LIVEBUFFER) / "roku_playback"


def _stop_roku_transcoder_locked():
    global _roku_playback_process, _roku_playback_log_handle

    process = _roku_playback_process
    _roku_playback_process = None

    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass

    if _roku_playback_log_handle is not None:
        try:
            _roku_playback_log_handle.close()
        except Exception:
            pass
        _roku_playback_log_handle = None


def _resolve_roku_source_playlist(active, playlist_value=None):
    session_dir = Path(active["session_dir"])
    playlist_value = str(playlist_value or "live.m3u8")

    if playlist_value.startswith("/livebuffer/sessions/"):
        relative = playlist_value[len("/livebuffer/"):]
        return (Path(config.LIVEBUFFER) / relative).resolve()

    candidate = Path(playlist_value)
    if candidate.is_absolute():
        return candidate.resolve()

    return (session_dir / candidate.name).resolve()


def _start_roku_transcoder(active, source_playlist, mode):
    global _roku_playback_process, _roku_playback_log_handle

    source_playlist = Path(source_playlist).resolve()
    if not source_playlist.exists():
        raise FileNotFoundError(f"Roku source playlist not found: {source_playlist}")

    output_dir = _roku_output_dir()

    with _roku_playback_lock:
        _stop_roku_transcoder_locked()

        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        log_path = Path(config.LOGS) / "roku_playback_ffmpeg.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _roku_playback_log_handle = open(log_path, "ab", buffering=0)

        output_playlist = output_dir / "live.m3u8"
        segment_pattern = output_dir / "segment_%06d.ts"

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-y",
            "-fflags", "+genpts+discardcorrupt",
            "-i", str(source_playlist),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            # High-quality Roku profile. bwdif send_field converts 1080i/480i
            # to smooth 59.94p/59.94p progressive video instead of discarding
            # one temporal field. Lanczos is only used when dimensions must be
            # made even for H.264; native source resolution is otherwise kept.
            "-vf", (
                "bwdif=mode=send_field:parity=auto:deint=interlaced,"
                "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-profile:v", "high",
            "-level", "4.2",
            "-pix_fmt", "yuv420p",
            # Roku requires a declared/valid stream bitrate. Use a high-quality
            # constrained VBR target instead of CRF-only mode so the player can
            # identify the stream bitrate correctly.
            "-b:v", "9000k",
            "-maxrate", "12000k",
            "-bufsize", "24000k",
            "-g", "120",
            "-keyint_min", "120",
            "-sc_threshold", "0",
            "-force_key_frames", "expr:gte(t,n_forced*2)",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",
            "-ar", "48000",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "12",
            "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments",
            "-hls_segment_filename", str(segment_pattern),
            str(output_playlist),
        ]

        _roku_playback_process = subprocess.Popen(
            command,
            stdout=_roku_playback_log_handle,
            stderr=_roku_playback_log_handle,
            cwd=str(output_dir),
        )

        deadline = time.time() + 15.0
        while time.time() < deadline:
            if _roku_playback_process.poll() is not None:
                raise RuntimeError(
                    "Roku H.264 transcoder exited early; check "
                    f"{log_path}"
                )

            if output_playlist.exists() and output_playlist.stat().st_size > 0:
                segment_count = len(list(output_dir.glob("segment_*.ts")))
                if segment_count >= 2:
                    break

            time.sleep(0.2)
        else:
            _stop_roku_transcoder_locked()
            raise RuntimeError(
                "Timed out waiting for Roku H.264 playlist; check "
                f"{log_path}"
            )

        _roku_playback_state.update({
            "channel": active.get("channel", ""),
            "session_id": active.get("session_id", ""),
            "mode": mode,
            "source_playlist": str(source_playlist),
            "output_dir": str(output_dir),
        })

    return {
        "ok": True,
        "channel": active.get("channel", ""),
        "session_id": active.get("session_id", ""),
        "mode": mode,
        "playlist_url": request.host_url.rstrip("/")
            + "/livebuffer/roku_playback/live.m3u8",
    }


@app.route("/api/roku/playback/start/<channel>", methods=["GET", "POST"])
def api_roku_playback_start(channel):
    try:
        # This is the same Playback Engine v2 session used by Android.  Roku's
        # only special handling is the final MPEG-2 -> H.264 presentation layer.
        active = stream_engine.start_or_reuse(channel)
        source_playlist = _resolve_roku_source_playlist(active, "live.m3u8")
        return jsonify(_start_roku_transcoder(active, source_playlist, "live"))
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/roku/playback/seek", methods=["GET", "POST"])
def api_roku_playback_seek():
    try:
        seconds = int(request.args.get("seconds", "-10") or -10)
        seconds = max(-3600, min(3600, seconds))

        result = playback_manager.seek(seconds)
        if not result:
            return jsonify({"ok": False, "error": "No active stream"}), 404

        active = result.get("active") or stream_engine.get_active()
        if not active:
            return jsonify({"ok": False, "error": "No active stream"}), 404

        source_playlist = _resolve_roku_source_playlist(
            active,
            result.get("playlist"),
        )
        response = _start_roku_transcoder(
            active,
            source_playlist,
            result.get("mode") or "seek",
        )
        response.update({
            "seconds": seconds,
            "seconds_behind": int(result.get("seconds_behind", 0) or 0),
            "live": bool(result.get("live")),
        })
        return jsonify(response)
    except ValueError:
        return jsonify({"ok": False, "error": "seconds must be an integer"}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/roku/playback/live", methods=["GET", "POST"])
def api_roku_playback_live():
    try:
        result = playback_manager.jump_live()
        if not result:
            return jsonify({"ok": False, "error": "No active stream"}), 404

        active = result.get("active") or stream_engine.get_active()
        if not active:
            return jsonify({"ok": False, "error": "No active stream"}), 404

        source_playlist = _resolve_roku_source_playlist(
            active,
            result.get("playlist") or "live.m3u8",
        )
        response = _start_roku_transcoder(active, source_playlist, "live")
        response["live"] = True
        return jsonify(response)
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/roku/playback/status")
def api_roku_playback_status():
    with _roku_playback_lock:
        running = (
            _roku_playback_process is not None
            and _roku_playback_process.poll() is None
        )
        return jsonify({
            "ok": True,
            "running": running,
            **_roku_playback_state,
            "playlist_url": (
                request.host_url.rstrip("/")
                + "/livebuffer/roku_playback/live.m3u8"
                if running else ""
            ),
        })


@app.route("/api/roku/playback/stop", methods=["POST"])
def api_roku_playback_stop():
    with _roku_playback_lock:
        _stop_roku_transcoder_locked()
        _roku_playback_state.update({
            "channel": "",
            "session_id": "",
            "mode": "stopped",
            "source_playlist": "",
            "output_dir": "",
        })
    return jsonify({"ok": True})


@app.route("/api/roku_test/start/<channel>", methods=["GET", "POST"])
def api_roku_test_start(channel):
    """
    TEMPORARY, test-only route for evaluating Roku playback.

    The main live pipeline (stream_engine / /api/live/start) passes
    video through untouched (-c:v copy), which for OTA/HDHomeRun
    sources is MPEG-2 - not a codec Roku's HLS player supports.

    stream_manager.py already has a working libx264 encode command
    (previously unused/dead code); this route just calls it, so we can
    verify Roku can actually play an H.264 HLS stream from this server
    before building anything else. It intentionally does NOT touch the
    stream_engine session that the Android app uses - completely
    separate pipeline, separate output directory (config.LIVEBUFFER),
    already served at /timeshiftbuffer/live.m3u8.

    Safe to delete once real Roku work starts and a proper endpoint
    replaces it.
    """
    try:
        ok = stream_manager.start_stream(channel)

        if not ok:
            return jsonify({
                "ok": False,
                "error": (
                    "ffmpeg didn't start in time - check "
                    f"{stream_manager.LOGFILE}"
                ),
            }), 500

        return jsonify({
            "ok": True,
            "channel": stream_manager.current_channel(),
            "playlist_url": "/timeshiftbuffer/live.m3u8",
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/roku_test/stop", methods=["POST"])
def api_roku_test_stop():
    stream_manager.stop_stream()
    return jsonify({"ok": True})


@app.route("/api/roku_test/now")
def api_roku_test_now():
    """
    Program start/stop times (see database.get_guide_grid_bulk) are
    compared against datetime.datetime.now() - naive server-local time,
    not UTC. Rather than have the Roku guess at the server's timezone to
    line up its own clock with those timestamps, it just asks the server
    directly, in the exact same format, and does all its grid-position
    math relative to this value instead of its own clock.
    """
    import datetime

    return jsonify({
        "now": datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
    })


@app.route("/api/live/status")
def api_live_status():
    active = stream_engine.get_active()

    if not active:
        return jsonify({
            "running": False,
            "channel": "",
            "playlist_exists": False,
            "playlist_url": "",
            "active": None,
        })

    session_dir = Path(active["session_dir"])
    playlist = session_dir / "live.m3u8"

    return jsonify({
        "running": True,
        "channel": active.get("channel", ""),
        "guide_name": active.get("guide_name", ""),
        "session_id": active.get("session_id", ""),
        "playlist_exists": (
            playlist.exists() and playlist.stat().st_size > 0
        ),
        "playlist_url": active.get("playlist_url", ""),
        "active": active,
    })


@app.route("/api/stream/active")
def api_stream_active():
    return jsonify(stream_engine.get_active())


@app.route("/api/live/segments")
def api_live_segments():
    active = stream_engine.get_active()
    session_id = request.args.get("session_id")
    channel = request.args.get("channel")

    if active:
        session_id = session_id or active.get("session_id")
        channel = channel or active.get("channel")
        live_segments.sync_active_segment(active)

    try:
        limit = int(request.args.get("limit", "50") or 50)
    except Exception:
        limit = 50

    return jsonify({
        "ok": True,
        "active": active,
        "segments": database.list_live_program_segments(
            session_id=session_id,
            channel=channel,
            limit=limit,
        ),
    })


@app.route("/api/stream/stop", methods=["POST"])
def api_stream_stop():
    stream_manager.stop_stream()
    stream_engine.stop_active()

    return jsonify({
        "ok": True,
        "active": None,
    })


@app.route("/api/stream/save", methods=["POST"])
def api_stream_save():
    try:
        active = stream_engine.get_active()
        filename = stream_recorder.save_buffer_minutes_as_recording(
            active,
            minutes=int(request.form.get("minutes", "5") or 5),
            title=request.form.get("title", "")
        
        )
        return jsonify({"ok": True, "filename": filename})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/livebuffer/<path:filename>")
def livebuffer(filename):
    return send_from_directory(config.LIVEBUFFER, filename, as_attachment=False)


@app.route("/play/live/<channel>")
def play_live_channel(channel):
    try:
        session = stream_engine.start_or_reuse(channel)
        return redirect(session["playlist_url"])
    except Exception as e:
        return str(e), 500
    
@app.route("/play/live/<channel>/web")
def play_live_channel_web(channel):
    try:
        ch = database.get_channel(channel)

        if not ch:
            return "Channel not found", 404

        # database.get_channel() already resolves the currently selected
        # tuner source. For HDHomeRun this is the tuner URL; for Native DVB
        # this is the native-dvb:// source handled by the web stream manager.
        source_url = str(
            ch.get("url") or ""
        ).strip()

        if not source_url:
            return "Channel has no playable source URL", 400

        client_id = web_stream_manager.client_id(
            request.remote_addr or "",
            request.headers.get("User-Agent", ""),
        )

        web_session = web_stream_manager.start(
            client_id=client_id,
            channel=ch["guide_number"],
            guide_name=ch["guide_name"],
            source_url=source_url,
        )

        return redirect(web_session.playlist_url())

    except Exception as e:
        return str(e), 500


@app.route("/api/web/live/status")
def api_web_live_status():
    client_id = web_stream_manager.client_id(
        request.remote_addr or "",
        request.headers.get("User-Agent", ""),
    )

    return jsonify({
        "ok": True,
        "session": web_stream_manager.status(client_id),
    })


@app.route("/api/web/live/heartbeat", methods=["POST"])
def api_web_live_heartbeat():
    client_id = web_stream_manager.client_id(
        request.remote_addr or "",
        request.headers.get("User-Agent", ""),
    )

    return jsonify({
        "ok": web_stream_manager.touch(client_id),
        "client_id": client_id,
    })


@app.route("/api/web/live/stop", methods=["POST"])
def api_web_live_stop():
    client_id = web_stream_manager.client_id(
        request.remote_addr or "",
        request.headers.get("User-Agent", ""),
    )

    stopped = web_stream_manager.stop(client_id)

    return jsonify({
        "ok": bool(stopped),
        "client_id": client_id,
    })


@app.route("/live/player/<channel>")
def live_player_page(channel):
    ch = database.get_channel(channel)

    if not ch:
        return "Channel not found", 404

    # Do not call stream_engine.start_or_reuse() here. Web playback owns a
    # separate H.264/AAC session and must never retune the Android live session.
    return render_template(
        "live_player.html",
        channel=channel,
        stream_url=f"/play/live/{channel}/web",
    )


@app.route("/play/live/<channel>/beginning")
def play_live_channel_from_beginning(channel):
    try:
        session = stream_engine.start_or_reuse(channel)

        result = playback.watch_from_beginning(session["session_dir"])

        if not result:
            return redirect(session["playlist_url"])

        playlist_name = playlist.build_playlist_from_segment(
            session["session_dir"],
            result["segment"],
            output_name="program_start.m3u8"
        )

        return redirect(f"/livebuffer/sessions/{session['session_id']}/{playlist_name}")

    except Exception as e:
        return str(e), 500

@app.route("/api/guide")
def api_guide():
    return jsonify(database.get_now_next())




# --------------------------------------------------
# Program Catalog API
# --------------------------------------------------

@app.route("/api/program_catalog")
def api_program_catalog():
    status = request.args.get("status", "")
    channel = request.args.get("channel", "")
    saved_arg = request.args.get("saved")

    saved = None
    if saved_arg is not None:
        saved = str(saved_arg).lower() in ("1", "true", "yes", "saved")

    try:
        limit = int(request.args.get("limit", "100") or 100)
    except Exception:
        limit = 100

    active = stream_engine.get_active()
    if active:
        try:
            live_segments.sync_active_segment(active)
        except Exception as e:
            print("program catalog sync error:", e, flush=True)

    return jsonify({
        "ok": True,
        "programs": program_catalog.list_programs(
            status=status,
            saved=saved,
            channel=channel,
            limit=limit,
        ),
    })


@app.route("/api/program_catalog/live")
def api_program_catalog_live():
    active = stream_engine.get_active()
    program = None

    if active:
        program = live_segments.sync_active_segment(active)

    return jsonify({
        "ok": True,
        "active": active,
        "program": program,
    })


@app.route("/api/program_catalog/buffered")
def api_program_catalog_buffered():
    try:
        limit = int(request.args.get("limit", "100") or 100)
    except Exception:
        limit = 100

    return jsonify({
        "ok": True,
        "programs": program_catalog.list_programs(status="buffered", limit=limit),
    })


@app.route("/api/program_catalog/saved")
def api_program_catalog_saved():
    try:
        limit = int(request.args.get("limit", "100") or 100)
    except Exception:
        limit = 100

    return jsonify({
        "ok": True,
        "programs": program_catalog.list_programs(saved=True, limit=limit),
    })


@app.route("/api/program_catalog/<int:program_id>")
def api_program_catalog_item(program_id):
    program = program_catalog.get_program(program_id)

    if not program:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    return jsonify({"ok": True, "program": program})


@app.route("/api/program_catalog/<int:program_id>/save", methods=["POST"])
def api_program_catalog_save(program_id):
    try:
        program = program_catalog.save_program(program_id)

        if not program:
            return jsonify({"ok": False, "error": "Program not found"}), 404

        response = {
            "ok": True,
            "program": program,
            "preserved": str(program.get("status") or "").lower() == "saved",
        }

        # If it was preserved immediately, also include a playable playlist URL.
        if str(program.get("status") or "").lower() == "saved":
            try:
                result = program_catalog.build_program_playlist(program_id)
                base = request.host_url.rstrip("/")
                playlist_url = (
                    f"{base}/api/program_catalog/"
                    f"{int(program_id)}/media/"
                    f"{result['playlist']}"
                )

                response.update({
                    "playlist": result.get("playlist"),
                    "playlist_url": playlist_url,
                    "segment_count": result.get("segment_count"),
                    "clamped": bool(result.get("clamped")),
                })
            except Exception as playlist_error:
                response["playlist_error"] = str(playlist_error)

        return jsonify(response)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/program_catalog/<int:program_id>/expire", methods=["POST"])
def api_program_catalog_expire(program_id):
    program = program_catalog.expire_program(program_id)

    if not program:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    return jsonify({"ok": True, "program": program})


@app.route(
    "/api/program_catalog/<int:program_id>/media/<path:filename>"
)
def api_program_catalog_media(program_id, filename):
    program = program_catalog.get_program(program_id)

    if not program:
        return jsonify({
            "ok": False,
            "error": "Program not found",
        }), 404

    try:
        folder = program_catalog._resolve_program_folder(
            program
        )
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 400

    requested = (
        folder
        / str(filename or "")
    ).resolve(strict=False)

    try:
        requested.relative_to(folder)
    except ValueError:
        return jsonify({
            "ok": False,
            "error": "Invalid media path",
        }), 400

    if not requested.exists() or not requested.is_file():
        return jsonify({
            "ok": False,
            "error": "Media file not found",
        }), 404

    mimetype = None

    if requested.suffix.lower() == ".m3u8":
        mimetype = "application/vnd.apple.mpegurl"
    elif requested.suffix.lower() == ".ts":
        mimetype = "video/mp2t"

    return send_from_directory(
        str(folder),
        requested.name,
        mimetype=mimetype,
        conditional=True,
    )


@app.route("/api/program_catalog/<int:program_id>/playlist")
def api_program_catalog_playlist(program_id):
    try:
        result = program_catalog.build_program_playlist(program_id)
        program = result["program"]
        base = request.host_url.rstrip("/")
        session_dir = Path(result["session_dir"])

        playlist_url = (
            f"{base}/api/program_catalog/"
            f"{int(program_id)}/media/"
            f"{result['playlist']}"
        )

        return jsonify({
            "ok": True,
            "program": program,
            "playlist": result["playlist"],
            "playlist_url": playlist_url,
            "clamped": bool(result.get("clamped")),
            "requested_first_segment": result.get("requested_first_segment"),
            "actual_first_segment": result.get("actual_first_segment"),
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/api/program_catalog/<int:program_id>/seek")
def api_program_catalog_seek(program_id):
    try:
        position = float(request.args.get("position", "0") or 0)
        seconds = int(request.args.get("seconds", "0") or 0)

        result = program_catalog.seek_program_playlist(
            program_id,
            position_seconds=position,
            delta_seconds=seconds,
        )

        program = result["program"]
        base = request.host_url.rstrip("/")
        session_dir = Path(result["session_dir"])

        playlist_url = (
            f"{base}/api/program_catalog/"
            f"{int(program_id)}/media/"
            f"{result['playlist']}"
        )

        return jsonify({
            "ok": True,
            "program": program,
            "playlist": result["playlist"],
            "playlist_url": playlist_url,
            "target_segment": result.get("target_segment"),
            "target_seconds": result.get("target_seconds"),
            "position_seconds": result.get("position_seconds"),
            "delta_seconds": result.get("delta_seconds"),
            "segment_count": result.get("segment_count"),
            "program_segment_count": result.get("program_segment_count"),
            "clamped": bool(result.get("clamped")),
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/api/program_catalog/<int:program_id>/vod")
def api_program_catalog_vod(program_id):
    try:
        profile = request.args.get("profile", "browser").strip().lower()

        if profile != "browser":
            return jsonify({
                "ok": False,
                "error": "Program Catalog web playback supports browser profile only",
            }), 400

        force = str(request.args.get("force", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        source_result = program_catalog.build_program_playlist(program_id)
        source_playlist = (
            Path(source_result["session_dir"])
            / source_result["playlist"]
        ).resolve()

        result = vod.build_program_browser_vod(
            source_playlist=source_playlist,
            force=force,
        )

        base = request.host_url.rstrip("/")

        return jsonify({
            **result,
            "program_id": int(program_id),
            "profile": "browser",
            "playlist_url": (
                f"{base}/api/program_catalog/"
                f"{int(program_id)}/browser-media/index.m3u8"
            ),
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route(
    "/api/program_catalog/<int:program_id>/browser-media/<path:filename>"
)
def api_program_catalog_browser_media(program_id, filename):
    try:
        source_result = program_catalog.build_program_playlist(program_id)
        source_playlist = (
            Path(source_result["session_dir"])
            / source_result["playlist"]
        ).resolve()

        output_dir = vod.program_browser_vod_directory(
            source_playlist
        ).resolve()

        requested = (
            output_dir
            / str(filename or "")
        ).resolve(strict=False)

        try:
            requested.relative_to(output_dir)
        except ValueError:
            return jsonify({
                "ok": False,
                "error": "Invalid browser media path",
            }), 400

        if not requested.exists() or not requested.is_file():
            return jsonify({
                "ok": False,
                "error": "Browser media file not found",
            }), 404

        mimetype = None

        if requested.suffix.lower() == ".m3u8":
            mimetype = "application/vnd.apple.mpegurl"
        elif requested.suffix.lower() == ".ts":
            mimetype = "video/mp2t"
        elif requested.suffix.lower() == ".json":
            mimetype = "application/json"

        return send_from_directory(
            str(output_dir),
            requested.name,
            mimetype=mimetype,
            conditional=True,
        )

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/program/player/<int:program_id>")
def web_program_player(program_id):
    try:
        program = program_catalog.get_program(program_id)

        if not program:
            return "Program not found", 404

        return render_template(
            "web_player.html",
            title=program.get("title") or "SignalDVR",
            subtitle=(
                program.get("guide_name")
                or program.get("channel")
                or ""
            ),
            playlist_url=None,
            vod_api_url=(
                f"/api/program_catalog/{int(program_id)}/vod"
                "?profile=browser"
            ),
            back_url="/library",
        )

    except Exception as e:
        return str(e), 500


@app.route("/recording/player/<int:recording_id>")
def web_recording_player(recording_id):
    try:
        item = None
        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                item = dict(row)
                break

        if not item:
            return "Recording not found", 404

        return render_template(
            "web_player.html",
            title=item.get("title") or item.get("filename") or "Recording",
            subtitle=item.get("channel") or "",
            playlist_url=None,
            vod_api_url=(
                f"/api/recordings/{int(recording_id)}/vod"
                "?profile=browser"
            ),
            back_url="/recordings",
        )

    except Exception as e:
        return str(e), 500


@app.route("/api/program_catalog/<int:program_id>/delete", methods=["DELETE", "POST"])
def api_program_catalog_delete(program_id):
    try:
        program = program_catalog.get_program(program_id)

        if not program:
            return jsonify({"ok": False, "error": "Program not found"}), 404

        file_path = Path(program.get("file_path") or "")
        base_recordings = config.RECORDINGS.resolve()

        deleted_files = False
        if file_path.exists():
            try:
                resolved = file_path.resolve()
                if base_recordings in resolved.parents or resolved == base_recordings:
                    shutil.rmtree(resolved, ignore_errors=True)
                    deleted_files = True
            except Exception as file_error:
                print("Program media delete error:", file_error, flush=True)

        deleted = database.delete_live_program_segment(program_id)

        return jsonify({
            "ok": True,
            "deleted": deleted,
            "deleted_files": deleted_files,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500






@app.route(
    "/api/program_catalog/<int:program_id>/download"
)
def api_program_catalog_download(program_id):
    try:
        return download_service.download_program(program_id)

    except FileNotFoundError as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 404

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


@app.route(
    "/api/recordings/<int:recording_id>/download"
)
def api_recording_download(recording_id):
    try:
        return download_service.download_recording(recording_id)

    except FileNotFoundError as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 404

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500

@app.route("/api/program_catalog/<int:program_id>/resume")
def api_program_resume_get(program_id):
    try:
        program = program_catalog.get_program(program_id)

        if not program:
            return jsonify({
                "ok": False,
                "error": "Program not found",
            }), 404

        folder = program_catalog._resolve_program_folder(program)

        result = resume_manager.load_resume(
            item_id=program_id,
            media_path=folder,
            duration_seconds=0.0,
            item_type="program",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route(
    "/api/program_catalog/<int:program_id>/resume",
    methods=["POST"],
)
def api_program_resume_save(program_id):
    try:
        program = program_catalog.get_program(program_id)

        if not program:
            return jsonify({
                "ok": False,
                "error": "Program not found",
            }), 404

        folder = program_catalog._resolve_program_folder(program)
        payload = request.get_json(silent=True) or request.form

        position_seconds = float(
            payload.get("position_seconds", 0) or 0
        )
        duration_seconds = float(
            payload.get("duration_seconds", 0) or 0
        )

        completed_raw = payload.get("completed")
        completed = None

        if completed_raw is not None:
            completed = str(completed_raw).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        result = resume_manager.save_resume(
            item_id=program_id,
            media_path=folder,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            completed=completed,
            item_type="program",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except ValueError:
        return jsonify({
            "ok": False,
            "error": (
                "position_seconds and duration_seconds "
                "must be numbers"
            ),
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route(
    "/api/program_catalog/<int:program_id>/resume",
    methods=["DELETE"],
)
def api_program_resume_clear(program_id):
    try:
        program = program_catalog.get_program(program_id)

        if not program:
            return jsonify({
                "ok": False,
                "error": "Program not found",
            }), 404

        folder = program_catalog._resolve_program_folder(program)

        result = resume_manager.clear_resume(
            item_id=program_id,
            media_path=folder,
            item_type="program",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/vod")
def api_recording_vod(recording_id):
    try:
        profile = request.args.get("profile", "vlc").strip().lower()

        if profile not in ("vlc", "media3", "browser"):
            return jsonify({
                "ok": False,
                "error": "profile must be vlc, media3, or browser",
            }), 400

        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        if not filename:
            return jsonify({
                "ok": False,
                "error": "Recording has no filename",
            }), 400

        result = vod.build_vod(
            filename=filename,
            profile=profile,
            force=False,
        )

        base = request.host_url.rstrip("/")

        return jsonify({
            **result,
            "recording_id": recording_id,
            "title": recording.get("title") or filename,
            "playlist_url": base + result["playlist_url"],
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/resume")
def api_recording_resume_get(recording_id):
    try:
        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        if not filename:
            return jsonify({
                "ok": False,
                "error": "Recording has no filename",
            }), 400

        index_data = playback_index.get_or_build_index(filename)
        duration = float(index_data.get("duration_seconds") or 0)

        result = resume_manager.load_resume(
            item_id=recording_id,
            media_path=config.RECORDINGS / filename,
            duration_seconds=duration,
            item_type="recording",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/resume", methods=["POST"])
def api_recording_resume_save(recording_id):
    try:
        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        if not filename:
            return jsonify({
                "ok": False,
                "error": "Recording has no filename",
            }), 400

        payload = request.get_json(silent=True) or request.form

        position_seconds = float(
            payload.get("position_seconds", 0) or 0
        )

        duration_seconds = float(
            payload.get("duration_seconds", 0) or 0
        )

        completed_raw = payload.get("completed")
        completed = None

        if completed_raw is not None:
            completed = str(completed_raw).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        result = resume_manager.save_resume(
            item_id=recording_id,
            media_path=config.RECORDINGS / filename,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            completed=completed,
            item_type="recording",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except ValueError:
        return jsonify({
            "ok": False,
            "error": "position_seconds and duration_seconds must be numbers",
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/resume", methods=["DELETE"])
def api_recording_resume_clear(recording_id):
    try:
        recording = None

        for row in database.list_recordings():
            if int(row["id"]) == int(recording_id):
                recording = dict(row)
                break

        if not recording:
            return jsonify({
                "ok": False,
                "error": "Recording not found",
            }), 404

        filename = recording.get("filename") or ""

        result = resume_manager.clear_resume(
            item_id=recording_id,
            media_path=config.RECORDINGS / filename,
            item_type="recording",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/<int:recording_id>/delete", methods=["DELETE", "POST"])
def api_recording_delete(recording_id):
    try:
        recording = database.delete_recording_by_id(recording_id)

        if not recording:
            return jsonify({"ok": False, "error": "Recording not found"}), 404

        filename = recording.get("filename") or ""
        deleted_file = False

        if filename:
            path = config.RECORDINGS / filename
            if path.exists():
                path.unlink()
                deleted_file = True

            stem = path.stem
            for thumb in config.THUMBNAILS.glob(stem + ".*"):
                try:
                    thumb.unlink()
                except Exception as thumb_error:
                    print("Thumbnail delete error:", thumb_error, flush=True)

            # Remove every generated VOD profile (Media3/VLC) for this source.
            # This is cache cleanup only and does not change playback behavior.
            try:
                deleted_vod = vod.delete_vod(filename)
            except Exception as vod_error:
                deleted_vod = False
                print("VOD delete error:", vod_error, flush=True)
        else:
            deleted_vod = False

        return jsonify({
            "ok": True,
            "recording": recording,
            "deleted_file": deleted_file,
            "deleted_vod": deleted_vod,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/api/programs")
def api_programs():
    return jsonify(database.get_programs())


@app.route("/api/tuner-sources", methods=["GET"])
def api_tuner_sources():
    return jsonify({
        "ok": True,
        "hdhomerun_enabled": (
            database.get_setting(
                "hdhomerun_enabled",
                "1",
            ) == "1"
        ),
        "native_dvb_enabled": (
            database.get_setting(
                "native_dvb_enabled",
                "0",
            ) == "1"
        ),
    })


@app.route(
    "/api/tuner-sources/<source>/enabled",
    methods=["POST"],
)
def api_set_tuner_source_enabled(source):
    source = str(
        source or ""
    ).strip().lower()

    setting_map = {
        "hdhomerun": "hdhomerun_enabled",
        "native_dvb": "native_dvb_enabled",
    }

    if source not in setting_map:
        return jsonify({
            "ok": False,
            "error": "Unknown tuner source",
        }), 404

    body = request.get_json(
        silent=True
    ) or {}

    if "enabled" not in body:
        return jsonify({
            "ok": False,
            "error": "Missing enabled value",
        }), 400

    raw_enabled = body["enabled"]

    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    else:
        enabled = str(
            raw_enabled
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    database.set_setting(
        setting_map[source],
        "1" if enabled else "0",
    )

    return jsonify({
        "ok": True,
        "source": source,
        "enabled": enabled,
        "hdhomerun_enabled": (
            database.get_setting(
                "hdhomerun_enabled",
                "1",
            ) == "1"
        ),
        "native_dvb_enabled": (
            database.get_setting(
                "native_dvb_enabled",
                "0",
            ) == "1"
        ),
    })


@app.route("/api/tuners")
def api_tuners():
    return jsonify(tuner_manager.status())


@app.route("/api/tuners/realtime")
def api_tuners_realtime():
    try:
        return jsonify(tuner_manager.realtime_status())
    except Exception as error:
        print(f"Tuner realtime status error: {error}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


@app.route("/api/tuners/status")
def api_tuners_status():
    try:
        return jsonify(tuner_manager.realtime_status())
    except Exception as error:
        print(f"Tuner status error: {error}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(error),
        }), 500


@app.route("/api/live/channel-links")
def api_live_channel_links():
    channels = database.list_channels()
    return jsonify([
        {
            "channel": c["guide_number"],
            "name": c["guide_name"],
            "play_url": f"/play/live/{c['guide_number']}",
            "guide_url": f"/guide-view/{c['guide_number']}",
        }
        for c in channels
        if c["enabled"] == 1
    ])

@app.route("/api/live/channels")
def api_live_channels():
    channels = database.list_channels()
    now_next_rows = database.get_now_next()
    base = request.host_url.rstrip("/")

    now_next_by_channel = {
        str(row["guide_number"]): row
        for row in now_next_rows
    }

    results = []

    for c in channels:
        if c["enabled"] != 1:
            continue

        guide_number = str(c["guide_number"])
        guide = now_next_by_channel.get(guide_number, {})

        results.append({
            "channel": guide_number,
            "number": guide_number,
            "name": c["guide_name"],
            "guide_name": c["guide_name"],
            "play_url": f"{base}/play/live/{guide_number}",
            "stream_url": f"{base}/play/live/{guide_number}",
            "logo": (
                f"{base}/static/logos/"
                f"{guide_number.replace('.', '_')}.png"
            ),
            "now_title": guide.get("now_title") or "",
            "now_start": guide.get("now_start") or "",
            "now_stop": guide.get("now_stop") or "",
            "next_title": guide.get("next_title") or "",
            "next_start": guide.get("next_start") or "",
            "next_stop": guide.get("next_stop") or "",
        })

    return jsonify(results)

@app.route("/api/live/stream/<channel>")
def api_live_stream(channel):
    try:
        session = stream_engine.start_or_reuse(channel)
        ch = database.get_channel(channel)

        base = request.host_url.rstrip("/")
        url = base + session["playlist_url"]

        return jsonify({
            "url": url,
            "channel": channel,
            "name": ch["guide_name"] if ch else channel
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "url": "",
            "channel": channel,
            "name": channel
        }), 500

@app.route("/api/live/timeshift/start/<channel>")
def api_live_timeshift_start(channel):
    try:
        session = stream_engine.start_or_reuse(channel)

        base = request.host_url.rstrip("/")
        url = base + session["playlist_url"]

        return jsonify({
            "ok": True,
            "hls_url": url,
            "raw_url": url,
            "file_url": url,
            "stream_url": url,
            "file": ""
        })


    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "hls_url": "",
            "raw_url": "",
            "file_url": "",
            "stream_url": "",
            "file": ""
        }), 500





def _program_with_artwork_url(row):
    if not row:
        return None

    item = dict(row)
    artwork_value = str(item.get("artwork") or "").strip()
    artwork_flag = artwork_value.lower()
    program_id = str(item.get("programid") or "").strip()

    if program_id and artwork_flag in ("true", "1", "yes"):
        item["artwork"] = (
            "/api/program-artwork/"
            + quote(program_id, safe="")
        )
    elif artwork_flag.startswith(("http://", "https://", "/")):
        item["artwork"] = artwork_value
    else:
        item["artwork"] = None

    return item

@app.route("/api/program-artwork/<program_id>")
def api_program_artwork(program_id):
    filename = None

    try:
        filename = tvmaze_artwork.cache_program_artwork(
            program_id
        )
    except Exception as error:
        print(
            f"TVmaze artwork lookup failed for "
            f"{program_id}: {error}",
            flush=True,
        )

    if not filename:
        return send_from_directory(
            tvmaze_artwork.PROGRAM_ARTWORK_DIR,
            "default_tv.jpg",
            as_attachment=False,
            max_age=86400,
        )

    return send_from_directory(
        tvmaze_artwork.PROGRAM_ARTWORK_DIR,
        filename,
        as_attachment=False,
        max_age=86400,
    )



@app.route("/api/live/nowplaying/<channel>")
def api_live_nowplaying(channel):
    ch = database.get_channel(channel)

    programs = database.get_programs_for_channel(channel, limit=2)
    rows = []
    for program in programs:
        item = _program_with_artwork_url(program)
        recording = database.get_program_recording_details(program)
        item["recording_status"] = recording.get("status", "none")
        item["recording_series"] = bool(recording.get("series"))
        item["recording_series_id"] = recording.get("series_id")
        rows.append(item)

    return jsonify({
        "channel": channel,
        "name": ch["guide_name"] if ch else channel,
        "current": rows[0] if len(rows) > 0 else None,
        "next": rows[1] if len(rows) > 1 else None
    })

_guide_grid_cache_lock = threading.Lock()
_guide_grid_cache = {}

# Guide data changes on the EPG refresh cycle. Keep each requested channel
# page briefly cached so reopening the guide remains instant without forcing
# the first 12-row request to build the full lineup.
_GUIDE_GRID_CACHE_TTL_SECONDS = 20


def _compute_guide_grid_trimmed(channel_offset=0, channel_limit=None):
    grid = database.get_guide_grid_bulk(
        limit_programs=200,
        channel_offset=channel_offset,
        channel_limit=channel_limit,
    )
    maps = database.get_recording_lookup_maps()
    base = request.host_url.rstrip("/")

    needed_fields = {
        "id", "title", "subtitle", "description", "start", "stop",
        "channel", "category", "episode", "rating", "is_new", "artwork",
        "recording_status", "recording_series", "recording_series_id",
    }

    result = {}
    for channel, programs in grid.items():
        rows = []

        for program in programs:
            item = _program_with_artwork_url(program)
            recording = database.resolve_recording_details(program, maps)
            item["recording_status"] = recording.get("status", "none")
            item["recording_series"] = bool(recording.get("series"))
            item["recording_series_id"] = recording.get("series_id")

            artwork = item.get("artwork")
            if artwork and artwork.startswith("/"):
                item["artwork"] = base + artwork

            rows.append({k: v for k, v in item.items() if k in needed_fields})

        result[channel] = rows

    return result


def _get_cached_guide_grid(channel_offset=0, channel_limit=None):
    now = time.time()
    key = (int(channel_offset or 0), channel_limit)

    with _guide_grid_cache_lock:
        cached = _guide_grid_cache.get(key)
        if cached is not None:
            age = now - cached["computed_at"]
            if age < _GUIDE_GRID_CACHE_TTL_SECONDS:
                return cached["data"]

    fresh = _compute_guide_grid_trimmed(
        channel_offset=channel_offset,
        channel_limit=channel_limit,
    )

    with _guide_grid_cache_lock:
        _guide_grid_cache[key] = {
            "data": fresh,
            "computed_at": now,
        }

    return fresh


@app.route("/api/live/guide/grid")
def api_live_guide_grid():
    """Return one progressively loadable page of guide channel rows.

    With no parameters this remains backward-compatible and returns the full
    grid. Android requests ``offset=0&limit=12`` first, renders that page, and
    then requests the remaining rows with ``offset=12`` in the background.
    """
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0

    raw_limit = request.args.get("limit")
    try:
        limit = None if raw_limit in (None, "") else max(1, min(200, int(raw_limit)))
    except (TypeError, ValueError):
        limit = None

    result = _get_cached_guide_grid(
        channel_offset=offset,
        channel_limit=limit,
    )
    payload = json.dumps(result)

    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        compressed = gzip.compress(payload.encode("utf-8"))
        response = app.response_class(compressed, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        return response

    return app.response_class(payload, mimetype="application/json")


@app.route("/api/live/guide/<channel>")
def api_live_guide(channel):
    programs = database.get_programs_for_channel(channel, limit=200)

    rows = []

    for program in programs:
        item = _program_with_artwork_url(program)
        recording = database.get_program_recording_details(program)
        item["recording_status"] = recording.get("status", "none")
        item["recording_series"] = bool(recording.get("series"))
        item["recording_series_id"] = recording.get("series_id")

        artwork = item.get("artwork")
        if artwork and artwork.startswith("/"):
            item["artwork"] = (
                request.host_url.rstrip("/") + artwork
            )

        rows.append(item)

    return jsonify(rows)

@app.route("/api/recordings/list")
def api_recordings_list():
    recordings = []

    for r in database.list_recordings():
        item = dict(r)
        item["play_url"] = f"/recording/play/{item['filename']}"
        item["download_url"] = f"/play/{item['filename']}"
        recordings.append(item)

    return jsonify(recordings)




@app.route("/api/recordings/start/<channel>", methods=["POST"])
def api_recordings_start(channel):
    """Schedule the current guide program for recording.

    This route intentionally uses the scheduler-managed recording path rather
    than the legacy global Recorder instance. The scheduler owns the recording,
    applies the configured padding, preserves guide metadata, and stops it at
    the scheduled end time.
    """
    try:
        program = database.get_current_program(channel)

        if not program:
            return jsonify({
                "ok": False,
                "recording": False,
                "channel": channel,
                "error": "No current guide program found",
            }), 404

        program_id = program.get("id")

        if not program_id:
            return jsonify({
                "ok": False,
                "recording": False,
                "channel": channel,
                "error": "Current guide program has no database ID",
            }), 500

        result = database.schedule_guide_program_once(int(program_id))

        if not result:
            return jsonify({
                "ok": False,
                "recording": False,
                "channel": channel,
                "error": "Unable to schedule current program",
            }), 500

        status = str(result.get("status") or "Scheduled")

        return jsonify({
            "ok": True,
            "recording": status.lower() == "recording",
            "scheduled": True,
            "channel": channel,
            "title": program.get("title") or "",
            "program_id": int(program_id),
            "schedule_id": result.get("schedule_id"),
            "series_id": result.get("series_id"),
            "status": status,
            "start": program.get("start") or program.get("start_time") or "",
            "stop": program.get("stop") or program.get("stop_time") or "",
            "message": "Recording scheduled through SignalDVR scheduler",
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "recording": False,
            "scheduled": False,
            "channel": channel,
            "error": str(e),
        }), 500


@app.route("/api/recordings/stop", methods=["POST"])
def api_recordings_stop():
    """Cancel a scheduler-managed recording.

    The caller may provide schedule_id, program_id, or channel as JSON/form
    data. If none is supplied, the active scheduler entries are inspected and
    the single active recording is cancelled when it can be identified safely.
    """
    try:
        payload = request.get_json(silent=True) or request.form or {}

        schedule_id = payload.get("schedule_id")
        program_id = payload.get("program_id")
        channel = str(payload.get("channel") or "").strip()

        if schedule_id not in (None, ""):
            schedule_id = int(schedule_id)

        if program_id not in (None, ""):
            program_id = int(program_id)

        program = None

        if not schedule_id and program_id:
            program = database.get_guide_program(program_id)
            if not program:
                return jsonify({
                    "ok": False,
                    "recording": False,
                    "error": "Program not found",
                }), 404

            details = database.get_program_recording_details(program)
            schedule_id = details.get("schedule_id")

        if not schedule_id and channel:
            program = database.get_current_program(channel)
            if program:
                details = database.get_program_recording_details(program)
                schedule_id = details.get("schedule_id")
                program_id = program.get("id")

        if not schedule_id:
            active = [
                dict(row)
                for row in database.list_active_schedules()
                if str(row.get("status") or "").lower() == "recording"
            ]

            if len(active) == 1:
                schedule_id = active[0].get("id")
                channel = channel or str(active[0].get("channel") or "")
            elif len(active) > 1:
                return jsonify({
                    "ok": False,
                    "recording": True,
                    "error": (
                        "Multiple recordings are active; provide schedule_id "
                        "or channel"
                    ),
                    "active_recordings": active,
                }), 409

        if not schedule_id:
            return jsonify({
                "ok": True,
                "recording": False,
                "message": "No active scheduled recording found",
            })

        cancelled = scheduler_service.cancel_scheduled_recording(
            int(schedule_id)
        )

        if cancelled is False:
            return jsonify({
                "ok": False,
                "recording": True,
                "schedule_id": int(schedule_id),
                "error": "Scheduled recording could not be cancelled",
            }), 500

        return jsonify({
            "ok": True,
            "recording": False,
            "channel": channel,
            "program_id": program_id,
            "schedule_id": int(schedule_id),
            "status": "Cancelled",
            "message": "Recording cancelled",
        })

    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "recording": False,
            "error": "schedule_id and program_id must be integers",
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "recording": False,
            "error": str(e),
        }), 500


@app.route("/api/recordings/status")
def api_recordings_status():
    """Return scheduler-managed recording status.

    Supplying ?channel=<guide_number> returns status for the current program on
    that channel. Without a channel, all active scheduler entries are returned.
    """
    try:
        channel = str(request.args.get("channel") or "").strip()

        if channel:
            program = database.get_current_program(channel)

            if not program:
                return jsonify({
                    "ok": True,
                    "recording": False,
                    "scheduled": False,
                    "channel": channel,
                    "program": None,
                    "status": "none",
                })

            details = database.get_program_recording_details(program)
            status = str(details.get("status") or "none")

            return jsonify({
                "ok": True,
                "recording": status.lower() == "recording",
                "scheduled": status.lower() in ("scheduled", "recording"),
                "channel": channel,
                "program": _program_with_artwork_url(program),
                "program_id": program.get("id"),
                "schedule_id": details.get("schedule_id"),
                "series_id": details.get("series_id"),
                "status": status,
            })

        active = [dict(row) for row in database.list_active_schedules()]
        recording_rows = [
            row
            for row in active
            if str(row.get("status") or "").lower() == "recording"
        ]

        return jsonify({
            "ok": True,
            "recording": bool(recording_rows),
            "recording_count": len(recording_rows),
            "active_count": len(active),
            "active_recordings": recording_rows,
            "active_schedules": active,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "recording": False,
            "error": str(e),
        }), 500




# --------------------------------------------------
# Android guide recording actions
# --------------------------------------------------

@app.route("/api/guide/program/<int:program_id>/record-options")
def api_guide_record_options(program_id):
    program = database.get_guide_program(program_id)
    if not program:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    details = database.get_program_recording_details(program)
    return jsonify({
        "ok": True,
        "program_id": program_id,
        "recording": bool(details.get("recording")),
        "series": bool(details.get("series")),
        "recording_status": details.get("status", "none"),
        "schedule_id": details.get("schedule_id"),
        "series_id": details.get("series_id"),
        "can_record": True,
    })


@app.route("/api/guide/program/<int:program_id>/record-once", methods=["POST"])
def api_guide_record_once(program_id):
    result = database.schedule_guide_program_once(program_id)
    if not result:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    return jsonify({
        "ok": True,
        "message": "Recording scheduled",
        "recording_status": result.get("status", "scheduled"),
        "schedule_id": result.get("schedule_id"),
        "series_id": result.get("series_id"),
    })


@app.route("/api/guide/program/<int:program_id>/record-series", methods=["POST"])
def api_guide_record_series(program_id):
    result = database.create_guide_series_rule(program_id, only_new=0)
    if not result:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    return jsonify({
        "ok": True,
        "message": "Series recording scheduled",
        "recording_status": result.get("status", "scheduled"),
        "schedule_id": result.get("schedule_id"),
        "series_id": result.get("series_id"),
    })


@app.route("/api/guide/program/<int:program_id>/record-new", methods=["POST"])
def api_guide_record_new(program_id):
    result = database.create_guide_series_rule(program_id, only_new=1)
    if not result:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    return jsonify({
        "ok": True,
        "message": "New episodes will be recorded",
        "recording_status": result.get("status", "scheduled"),
        "schedule_id": result.get("schedule_id"),
        "series_id": result.get("series_id"),
    })


@app.route("/api/guide/program/<int:program_id>/record", methods=["DELETE"])
def api_guide_cancel_recording(program_id):
    program = database.get_guide_program(program_id)
    if not program:
        return jsonify({"ok": False, "error": "Program not found"}), 404

    details = database.get_program_recording_details(program)
    schedule_id = details.get("schedule_id")

    if schedule_id and details.get("status") == "recording":
        scheduler_service.cancel_scheduled_recording(schedule_id)
    else:
        database.cancel_guide_program_schedule(program_id)

    updated = database.get_program_recording_details(program)
    return jsonify({
        "ok": True,
        "message": "Recording cancelled",
        "recording_status": updated.get("status", "none"),
        "schedule_id": updated.get("schedule_id"),
        "series_id": updated.get("series_id"),
    })



# --------------------------------------------------
# Scheduled Recordings API
# --------------------------------------------------

@app.route("/api/scheduled", methods=["GET"])
def api_list_scheduled_recordings():
    scheduled = database.list_upcoming_scheduled_recordings()

    return jsonify({
        "ok": True,
        "count": len(scheduled),
        "scheduled": scheduled,
    })


@app.route("/api/scheduled/<int:schedule_id>", methods=["GET"])
def api_get_scheduled_recording(schedule_id):
    item = database.get_scheduled_recording(schedule_id)

    if not item:
        return jsonify({
            "ok": False,
            "error": "Scheduled recording not found",
        }), 404

    return jsonify({
        "ok": True,
        "scheduled": item,
    })


@app.route("/api/scheduled/<int:schedule_id>", methods=["DELETE"])
def api_delete_scheduled_recording(schedule_id):
    item = database.get_scheduled_recording(schedule_id)

    if not item:
        return jsonify({
            "ok": False,
            "error": "Scheduled recording not found",
        }), 404

    status = str(item.get("status") or "").lower()

    if status == "recording":
        cancelled = scheduler_service.cancel_scheduled_recording(schedule_id)
        if cancelled is False:
            return jsonify({
                "ok": False,
                "error": "Active recording could not be cancelled",
                "schedule_id": schedule_id,
            }), 500
    else:
        database.delete_scheduled_recording(schedule_id)

    return jsonify({
        "ok": True,
        "schedule_id": schedule_id,
        "message": "Scheduled recording cancelled",
    })


@app.route("/api/series", methods=["GET"])
def api_list_series_rules():
    rules = database.list_series_recordings(include_disabled=True)

    return jsonify({
        "ok": True,
        "series": rules,
        "count": len(rules),
    })


@app.route("/api/series/<int:series_id>", methods=["POST"])
def api_update_series_rule(series_id):
    payload = request.get_json(silent=True) or {}

    rules = database.list_series_recordings(include_disabled=True)
    existing = next(
        (rule for rule in rules if int(rule.get("id", 0)) == series_id),
        None,
    )

    if existing is None:
        return jsonify({
            "ok": False,
            "error": "Series recording rule not found",
        }), 404

    def int_value(name, default):
        value = payload.get(name, default)

        if isinstance(value, bool):
            return 1 if value else 0

        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default or 0)

    title = str(payload.get("title", existing.get("title", "")) or "")
    channel = str(payload.get("channel", existing.get("channel", "")) or "")

    database.update_series_recording(
        series_id=series_id,
        title=title,
        channel=channel,
        only_new=int_value("only_new", existing.get("only_new", 0)),
        enabled=int_value("enabled", existing.get("enabled", 1)),
        priority=int_value("priority", existing.get("priority", 50)),
        start_padding=int_value(
            "start_padding",
            existing.get("start_padding", 2),
        ),
        end_padding=int_value(
            "end_padding",
            existing.get("end_padding", 5),
        ),
        keep_last=int_value("keep_last", existing.get("keep_last", 0)),
        any_channel=int_value(
            "any_channel",
            existing.get("any_channel", 0),
        ),
    )

    database.apply_series_rules()

    updated_rules = database.list_series_recordings(
        include_disabled=True
    )

    updated = next(
        (
            rule
            for rule in updated_rules
            if int(rule.get("id", 0)) == series_id
        ),
        None,
    )

    return jsonify({
        "ok": True,
        "message": "Series recording rule updated",
        "series": updated,
    })


@app.route("/api/series/<int:series_id>", methods=["DELETE"])
def api_delete_series_rule(series_id):
    database.delete_series_rule_and_future_schedules(series_id)
    return jsonify({
        "ok": True,
        "message": "Series recording cancelled",
        "series_id": series_id,
    })

@app.route("/api/record/options/<channel>")
def api_record_options(channel):
    import datetime

    program = database.get_current_program(channel)

    if not program:
        return jsonify({
            "ok": False,
            "error": "No current guide program found",
            "channel": channel
        }), 404

    return jsonify({
        "ok": True,
        "channel": channel,
        "program": program,
        "options": [
            "record_now",
            "record_rest",
            "record_whole",
            "schedule"
        ]
    })


@app.route("/api/record/rest/<channel>", methods=["POST"])
def api_record_rest(channel):
    import datetime

    program = database.get_current_program(channel)

    if not program:
        return jsonify({
            "ok": False,
            "error": "No current guide program found"
        }), 404

    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    database.add_scheduled_recording(
        channel=channel,
        title=program.get("title", ""),
        subtitle=program.get("subtitle", ""),
        start=now,
        stop=program.get("stop", ""),
        priority=100,
        start_padding=0,
        end_padding=0,
        description=program.get("description", ""),
        category=program.get("category", ""),
        episode=program.get("episode", ""),
        programid=program.get("programid", ""),
        seriesid=program.get("seriesid", ""),
        originalairdate=program.get("originalairdate", ""),
    )

    return jsonify({
        "ok": True,
        "mode": "record_rest",
        "channel": channel,
        "title": program.get("title", ""),
        "start": now,
        "stop": program.get("stop", ""),
        "message": "Scheduled to record the rest of this show"
    })


@app.route("/api/record/schedule/<channel>", methods=["POST"])
def api_record_schedule(channel):
    program = database.get_current_program(channel)

    if not program:
        return jsonify({
            "ok": False,
            "error": "No current guide program found"
        }), 404

    database.add_scheduled_recording(
        channel=channel,
        title=program.get("title", ""),
        subtitle=program.get("subtitle", ""),
        start=program.get("start", ""),
        stop=program.get("stop", ""),
        priority=100,
        start_padding=2,
        end_padding=5,
        description=program.get("description", ""),
        category=program.get("category", ""),
        episode=program.get("episode", ""),
        programid=program.get("programid", ""),
        seriesid=program.get("seriesid", ""),
        originalairdate=program.get("originalairdate", ""),
    )

    return jsonify({
        "ok": True,
        "mode": "schedule",
        "channel": channel,
        "title": program.get("title", ""),
        "start": program.get("start", ""),
        "stop": program.get("stop", ""),
        "message": "Scheduled this show from guide data"
    })



def _format_file_size(size_bytes):
    size = float(size_bytes or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _database_status():
    status = {
        "exists": database.DB.exists(),
        "size": "0 B",
        "integrity": "Unavailable",
        "schema_version": "Not set",
        "last_modified": "Unavailable",
        "created": "Unavailable",
        "table_count": 0,
    }

    if not database.DB.exists():
        return status

    try:
        stat = database.DB.stat()
        total_size = stat.st_size
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database.DB) + suffix)
            if sidecar.exists():
                total_size += sidecar.stat().st_size
        status["size"] = _format_file_size(total_size)
        status["last_modified"] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y %I:%M %p")
        # Linux commonly does not expose a reliable filesystem birth time.
        # Database creation time is read from the persistent settings table
        # below instead.
    except Exception:
        pass

    try:
        connection = sqlite3.connect(f"file:{database.DB.resolve()}?mode=ro", uri=True, timeout=10)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            status["integrity"] = "OK" if quick_check and str(quick_check[0]).lower() == "ok" else str(quick_check[0] if quick_check else "Unknown")
            user_version = connection.execute("PRAGMA user_version").fetchone()
            if user_version and int(user_version[0] or 0) > 0:
                status["schema_version"] = str(user_version[0])
            else:
                try:
                    row = connection.execute(
                        "SELECT value FROM settings WHERE key='schema_version'"
                    ).fetchone()
                    if row and str(row[0]).strip():
                        status["schema_version"] = str(row[0]).strip()
                except sqlite3.Error:
                    pass

            try:
                created_row = connection.execute(
                    "SELECT value FROM settings WHERE key='database_created_at'"
                ).fetchone()

                if created_row and str(created_row[0]).strip():
                    raw_created = str(created_row[0]).strip()

                    try:
                        created_dt = datetime.datetime.strptime(
                            raw_created,
                            "%Y-%m-%d %H:%M:%S",
                        )
                        status["created"] = created_dt.strftime(
                            "%b %d, %Y %I:%M %p"
                        )
                    except ValueError:
                        status["created"] = raw_created
            except sqlite3.Error:
                pass
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            status["table_count"] = int(table_count[0] if table_count else 0)
        finally:
            connection.close()
    except Exception as error:
        status["integrity"] = f"Error: {error}"

    return status

def _database_backup_directory():
    backup_dir = database.DB.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _create_database_snapshot(destination):
    """Create a consistent SQLite snapshot, including committed WAL data."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)

    source = sqlite3.connect(database.DB, timeout=30)
    target = sqlite3.connect(temporary)
    try:
        source.execute("PRAGMA busy_timeout=30000")
        source.backup(target)
        result = target.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {result}")
        target.commit()
    finally:
        target.close()
        source.close()

    os.replace(temporary, destination)
    return destination


def _validate_uploaded_database(path):
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=10)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise ValueError(f"SQLite integrity check failed: {result}")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not tables:
            raise ValueError("The uploaded SQLite database contains no tables.")
    finally:
        connection.close()


@app.route("/settings/database/backup", methods=["POST"])
def create_database_backup():
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"signaldvr-{timestamp}.db"
    try:
        _create_database_snapshot(_database_backup_directory() / filename)
        return redirect(f"/settings?database_backup={quote(filename)}")
    except Exception as error:
        return redirect(f"/settings?database_error={quote(str(error))}")


@app.route("/settings/database/download")
def download_current_database():
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot = _database_backup_directory() / f".download-{timestamp}-{os.getpid()}.db"
    try:
        _create_database_snapshot(snapshot)
    except Exception as error:
        return redirect(f"/settings?database_error={quote(str(error))}")

    @after_this_request
    def remove_snapshot(response):
        try:
            snapshot.unlink(missing_ok=True)
        except Exception:
            pass
        return response

    return send_file(
        snapshot,
        as_attachment=True,
        download_name=f"signaldvr-{timestamp}.db",
        mimetype="application/vnd.sqlite3",
        max_age=0,
    )


@app.route("/settings/database/backups/<path:filename>")
def download_database_backup(filename):
    backup_dir = _database_backup_directory().resolve()
    candidate = (backup_dir / Path(filename).name).resolve()
    if candidate.parent != backup_dir or not candidate.is_file():
        return "Backup not found", 404
    return send_file(
        candidate,
        as_attachment=True,
        download_name=candidate.name,
        mimetype="application/vnd.sqlite3",
        max_age=0,
    )


@app.route("/settings/database/restore", methods=["POST"])
def restore_database_backup():
    upload = request.files.get("database_file")
    if upload is None or not upload.filename:
        return redirect("/settings?database_error=Choose+a+database+backup+file+first")

    max_bytes = 2 * 1024 * 1024 * 1024
    database.DB.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.DB.parent / f".restore-upload-{os.getpid()}.db"
    temporary.unlink(missing_ok=True)

    try:
        total = 0
        with temporary.open("wb") as output:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Uploaded database exceeds the 2 GB safety limit.")
                output.write(chunk)

        if total == 0:
            raise ValueError("The uploaded database file is empty.")

        _validate_uploaded_database(temporary)

        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safety_name = f"pre-restore-{timestamp}.db"
        _create_database_snapshot(_database_backup_directory() / safety_name)

        try:
            with sqlite3.connect(database.DB, timeout=30) as current:
                current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        os.replace(temporary, database.DB)
        Path(str(database.DB) + "-wal").unlink(missing_ok=True)
        Path(str(database.DB) + "-shm").unlink(missing_ok=True)
        _validate_uploaded_database(database.DB)

        return redirect(
            f"/settings?database_restored=1&database_safety_backup={quote(safety_name)}"
        )
    except Exception as error:
        temporary.unlink(missing_ok=True)
        return redirect(f"/settings?database_error={quote(str(error))}")


@app.route("/settings")
def settings_page():
    import platform
    import sqlite3
    import sys

    settings = database.get_all_settings()
    sd_lineups = database.list_sd_lineups()

    try:
        backup_paths = sorted(
            (path for path in _database_backup_directory().glob("*.db") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:20]
        database_backups = [
            {
                "name": path.name,
                "size": _format_file_size(path.stat().st_size),
                "created": datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d, %Y %I:%M %p"),
            }
            for path in backup_paths
        ]
    except Exception:
        database_backups = []

    database_status = _database_status()
    last_database_backup = database_backups[0] if database_backups else None

    try:
        ffmpeg_output = subprocess.check_output(
            ["ffmpeg", "-version"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=3,
        )
        ffmpeg_version = ffmpeg_output.splitlines()[0].replace("ffmpeg version ", "FFmpeg ")
    except Exception:
        ffmpeg_version = "FFmpeg unavailable"

    guide_source = str(settings.get("guide_source", "") or "").strip().lower()
    guide_labels = {
        "schedules_direct": "Schedules Direct",
        "xmltv": "XMLTV",
    }

    system_info = {
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "platform": f"{platform.system()} {platform.release()}",
        "ffmpeg_version": ffmpeg_version,
        "guide_source": guide_labels.get(guide_source, guide_source or "Not configured"),
        "port": config.PORT,
        "background_dvr_enabled": str(settings.get("background_dvr_enabled", "1")) == "1",
        "thumbnail_path": str(config.THUMBNAILS),
    }

    return render_template(
        "settings.html",
        settings=settings,
        sd_lineups=sd_lineups,
        system_info=system_info,
        database_path=str(database.DB),
        database_status=database_status,
        last_database_backup=last_database_backup,
        database_backups=database_backups,
    )
@app.route("/settings/save", methods=["POST"])
def save_settings():
    storage_setting_keys = (
        "recording_path",
        "timeshift_path",
        "thumbnail_path",
    )

    previous_storage_paths = {
        key: str(database.get_setting(key, "") or "").strip()
        for key in storage_setting_keys
    }

    keys = [
        "dvr_name",
        "theme",

        "guide_source",
        "guide_days",
        "xmltv_path",
        "sd_username",
        "sd_password",
        "sd_country",
        "sd_postal_code",
        "sd_lineup",

        "android_player_engine",
        "default_start_padding",
        "default_end_padding",
        "default_priority",
        "default_keep_last",
        "skip_duplicates",
        "record_new_only",

        "max_recordings",

        "hdhr_host",
        "hdhr_max_tuners",
        "local_max_tuners",

        "recording_path",
        "timeshift_path",
        "thumbnail_path",

        "timeshift_enabled",
        "timeshift_minutes",
        "live_buffer_segments",
        "background_dvr_enabled",
        "background_dvr_retention",
        "background_dvr_storage_limit_gb",
        "background_dvr_when_full",
        "background_dvr_keep_saved",
        "background_dvr_show_library",
        "generate_thumbnails",

        "debug_logging",
    ]

    allowed_background_dvr_retention = {
        "end_of_day",
        "1_day",
        "3_days",
        "7_days",
        "14_days",
        "30_days",
        "never",
    }

    submitted_values = {}

    for key in keys:
        value = request.form.get(key, "")

        if key == "android_player_engine":
            value = (value or "vlc").strip().lower()

            if value not in ("vlc", "media3"):
                value = "vlc"

        elif key == "background_dvr_retention":
            value = (value or "end_of_day").strip().lower()
            if value not in allowed_background_dvr_retention:
                value = "end_of_day"

        elif key == "background_dvr_when_full":
            value = (value or "delete_oldest").strip().lower()
            if value not in {"delete_oldest", "stop_buffering"}:
                value = "delete_oldest"

        elif key == "background_dvr_storage_limit_gb":
            try:
                value = str(max(0, int(float(value or 250))))
            except (TypeError, ValueError):
                value = "250"

        elif key in {
            "background_dvr_enabled",
            "background_dvr_keep_saved",
            "background_dvr_show_library",
        }:
            value = "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"

        submitted_values[key] = value

    # Validate/create storage folders before storing the new paths. This keeps
    # invalid paths from replacing known-good settings.
    storage_checks = [
        ("recording_path", "Recording Folder"),
        ("timeshift_path", "Timeshift Folder"),
        ("thumbnail_path", "Thumbnail Folder"),
    ]

    storage_errors = []

    for setting_key, label in storage_checks:
        raw = str(submitted_values.get(setting_key, "") or "").strip()

        if not raw:
            continue

        path = Path(raw).expanduser()

        try:
            path.mkdir(
                parents=True,
                exist_ok=True,
            )

            if not os.access(path, os.W_OK):
                raise PermissionError(
                    "path exists but is not writable"
                )

        except Exception as error:
            storage_errors.append(
                f"{label}: {error}"
            )

    if storage_errors:
        error_message = " | ".join(storage_errors)

        return redirect(
            f"/settings?storage_error={quote(error_message)}"
        )

    for key, value in submitted_values.items():
        database.set_setting(key, value)

    # Register selected lineup with Schedules Direct.
    guide_source = submitted_values.get("guide_source", "")
    lineup = submitted_values.get("sd_lineup", "")

    if guide_source == "schedules_direct" and lineup:
        try:
            result = schedules_direct.add_lineup(lineup)

            print(
                "Schedules Direct lineup added:",
                result,
                flush=True,
            )

        except Exception as error:
            print(
                "Schedules Direct add lineup:",
                error,
                flush=True,
            )

    storage_changed = any(
        previous_storage_paths.get(key, "")
        != str(submitted_values.get(key, "") or "").strip()
        for key in storage_setting_keys
    )

    if storage_changed:
        print(
            "Storage settings changed; scheduling SignalDVR restart.",
            flush=True,
        )

        _restart_signaldvr_service_delayed()

        return redirect(
            "/settings?storage_saved=1&service_restarting=1"
        )

    if any(
        str(submitted_values.get(setting_key, "") or "").strip()
        for setting_key, _ in storage_checks
    ):
        return redirect("/settings?storage_saved=1")

    return redirect("/settings")


@app.route("/settings/sd/lineups", methods=["POST"])
def sd_get_lineups():
    keys = [
        "sd_username",
        "sd_password",
        "sd_country",
        "sd_postal_code",
        "sd_lineup",
        "guide_days",
        "guide_source",
    ]

    for key in keys:
        database.set_setting(key, request.form.get(key, ""))

    headends = schedules_direct.get_lineups()
    lineups = schedules_direct.flatten_lineups(headends)
    database.save_sd_lineups(lineups)
    database.set_setting("sd_last_lineup_refresh", "test")

    print(f"Schedules Direct lineups saved: {len(lineups)}", flush=True)
    return redirect("/settings")


@app.route("/settings/sd/download", methods=["POST"])
def sd_download_guide():
    keys = [
        "sd_username",
        "sd_password",
        "sd_country",
        "sd_postal_code",
        "sd_lineup",
        "guide_days",
        "guide_source",
    ]

    for key in keys:
        database.set_setting(key, request.form.get(key, ""))

    try:
        schedules_direct.cache_lineup_map(force=True)
        channel_count = schedules_direct.import_cached_channel_map()

        schedules_direct.cache_schedules_for_matched_channels(
            days=int(request.form.get("guide_days", 1) or 1),
            force=True,
        )
        program_cache, program_id_count = schedules_direct.cache_program_metadata(
            force=True
        )
        program_count = schedules_direct.import_cached_programs()

        print(
            f"Schedules Direct guide update complete: "
            f"channels={channel_count} "
            f"program_ids={program_id_count} "
            f"programs={program_count}",
            flush=True,
        )

    except Exception as e:
        print("Schedules Direct guide update error:", e, flush=True)

    return redirect("/settings")

@app.route("/tuners")
def tuners_page():
    return render_template("tuners.html", tuners=tuner_manager.status())


@app.route("/health")
def health():
    disk = shutil.disk_usage(config.RECORDINGS)
    tuners = tuner_manager.status()
    program_count = len(database.get_programs())
    scheduled = database.list_upcoming_scheduled_recordings()

    next_recording = None
    for s in scheduled:
        if s["status"] == "Scheduled":
            next_recording = s
            break

    try:
        tuner_debug = subprocess.check_output(
            ["hdhomerun_config", config.HDHR_DEVICE, "get", "/tuner1/debug"],
            text=True,
        )
    except Exception as e:
        tuner_debug = f"HDHomeRun error: {e}"

    return render_template(
        "health.html",
        disk=disk,
        tuners=tuners,
        program_count=program_count,
        next_recording=next_recording,
        tuner_debug=tuner_debug,
    )


if setup_service.is_complete():
    try:
        catalog_integrity.startup_check(auto_repair=True)
    except Exception as error:
        print(
            f"[SignalDVR] Background DVR catalog check failed: {error}",
            flush=True,
        )

    scheduler_service.start_scheduler()
    guide_service.start_guide_updater()
    cleanup_service.start_cleanup_service()
    artwork_service.start_artwork_service()
    recording_finalize.start_postprocess_service()

    # Start the live stream watchdog only after first-run setup.
    stream_engine.start_live_watchdog()
else:
    print(
        "[SignalDVR] First-run setup required; background services are paused.",
        flush=True,
    )

@app.route("/test")
def test():
    return render_template("test.html")

@app.route("/test/hls/<path:filename>")
def test_hls_recording(filename):
    from flask import send_from_directory, abort
    import subprocess
    from pathlib import Path

    rec_dir = Path("recordings").resolve()
    src = (rec_dir / filename).resolve()

    if not str(src).startswith(str(rec_dir)) or not src.exists():
        abort(404)

    out_dir = Path("livebuffer/test_hls") / filename.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / "index.m3u8"

    if not playlist.exists():
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_segment_filename", str(out_dir / "segment_%06d.ts"),
            str(playlist),
        ]
        subprocess.run(cmd, check=False)

    return send_from_directory(out_dir, "index.m3u8", mimetype="application/vnd.apple.mpegurl")




@app.route("/test/session/recording/<path:filename>")
def test_create_recording_session(filename):
    from flask import jsonify
    import subprocess
    import uuid
    import json
    from pathlib import Path

    rec_dir = Path("recordings").resolve()
    src = (rec_dir / filename).resolve()

    if not str(src).startswith(str(rec_dir)) or not src.exists():
        return jsonify({"ok": False, "error": "recording not found"}), 404

    session_id = uuid.uuid4().hex[:12]
    session_dir = Path("livebuffer/test_sessions") / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    playlist = session_dir / "index.m3u8"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_segment_filename", str(session_dir / "segment_%06d.ts"),
        str(playlist),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    meta = {
        "session_id": session_id,
        "type": "recording",
        "filename": filename,
        "source": str(src),
        "playlist": f"/livebuffer/test_sessions/{session_id}/index.m3u8",
        "returncode": result.returncode,
    }

    (session_dir / "session.json").write_text(json.dumps(meta, indent=2))

    if result.returncode != 0 or not playlist.exists():
        return jsonify({
            "ok": False,
            "error": "ffmpeg failed",
            "stderr": result.stderr[-2000:],
        }), 500

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "playlist": f"/livebuffer/test_sessions/{session_id}/index.m3u8",
        "vlc_url": f"/livebuffer/test_sessions/{session_id}/index.m3u8",
    })


@app.route("/test/session/<session_id>/info")
def test_session_info(session_id):
    from flask import jsonify
    from pathlib import Path
    import json

    session_dir = Path("livebuffer/test_sessions") / session_id
    meta_file = session_dir / "session.json"

    if not meta_file.exists():
        return jsonify({"ok": False, "error": "session not found"}), 404

    return jsonify({"ok": True, "session": json.loads(meta_file.read_text())})

@app.route("/test/hls/<path:filename>/<path:segment>")
def test_hls_recording_segment(filename, segment):
    from flask import send_from_directory, abort
    from pathlib import Path

    out_dir = Path("livebuffer/test_hls") / filename.replace("/", "_")

    if not out_dir.exists():
        abort(404)

    return send_from_directory(out_dir, segment)


@app.route("/test/hls_playlist/<path:filename>")
def test_hls_playlist(filename):
    from flask import send_from_directory, abort
    from urllib.parse import quote
    import subprocess
    from pathlib import Path

    rec_dir = Path("recordings").resolve()
    src = (rec_dir / filename).resolve()

    if not str(src).startswith(str(rec_dir)) or not src.exists():
        abort(404)

    safe_name = filename.replace("/", "_")
    out_dir = Path("livebuffer/test_hls") / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / "index.m3u8"

    if not playlist.exists():
        base_url = f"/test/hls_segment/{quote(filename)}/"

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_base_url", base_url,
            "-hls_segment_filename", str(out_dir / "segment_%06d.ts"),
            str(playlist),
        ]
        subprocess.run(cmd, check=False)

    return send_from_directory(out_dir, "index.m3u8", mimetype="application/vnd.apple.mpegurl")



@app.route("/test/make_hls/<path:filename>")
def test_make_hls(filename):
    from flask import jsonify
    import subprocess
    from pathlib import Path

    rec_dir = Path("recordings").resolve()
    src = (rec_dir / filename).resolve()

    if not str(src).startswith(str(rec_dir)) or not src.exists():
        return jsonify({"ok": False, "error": "recording not found"}), 404

    safe_name = filename.replace("/", "_")
    out_dir = Path("livebuffer/test_hls") / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / "index.m3u8"

    if not playlist.exists():
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_segment_filename", str(out_dir / "segment_%06d.ts"),
            str(playlist),
        ]
        subprocess.run(cmd, check=False)

    return jsonify({
        "ok": True,
        "playlist": f"/livebuffer/test_hls/{safe_name}/index.m3u8"
    })

@app.route("/test/hls_segment/<path:filename>/<path:segment>")
def test_hls_segment(filename, segment):
    from flask import send_from_directory, abort
    from pathlib import Path

    safe_name = filename.replace("/", "_")
    out_dir = Path("livebuffer/test_hls") / safe_name

    if not out_dir.exists():
        abort(404)

    return send_from_directory(out_dir, segment)

@app.route("/library")
def library_page():
    data = library.get_library()
    return render_template("library.html", library=data)

@app.route("/api/library")
def api_library():
    try:
        limit = int(request.args.get("limit", "100") or 100)
    except Exception:
        limit = 100

    return jsonify({
        "ok": True,
        **library.get_library(limit=limit),
    })


@app.route("/api/library/search")
def api_library_search():
    query = request.args.get("q", "").strip()

    try:
        limit = int(request.args.get("limit", "100") or 100)
    except (TypeError, ValueError):
        limit = 100

    limit = max(1, min(limit, 500))

    return jsonify({
        "ok": True,
        "query": query,
        **library.search_library(
            query=query,
            limit=limit,
        ),
    })

@app.route("/api/library/live")
def api_library_live():
    return jsonify({"ok": True, "live": library.get_live()})

@app.route("/api/android/settings")
def api_android_settings():
    player_engine = (
        database.get_setting("android_player_engine", "vlc")
        .strip()
        .lower()
    )

    if player_engine not in ("vlc", "media3"):
        player_engine = "vlc"

    return jsonify({
        "ok": True,
        "player_engine": player_engine,
    })


@app.route("/api/library/buffered")
def api_library_buffered():
    return jsonify({"ok": True, "buffered": library.get_buffered()})


@app.route("/api/library/saved")
def api_library_saved():
    return jsonify({"ok": True, "saved": library.get_saved()})


@app.route("/api/library/recorded")
def api_library_recorded():
    return jsonify({"ok": True, "recorded": library.get_recorded()})






if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, threaded=True)