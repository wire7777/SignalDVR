from flask import Flask, send_from_directory, redirect, render_template, jsonify, request

from app import config
from app import database
from app import epg
from app import hdhr
from app import search
from app import stream_manager
from app import thumbnails
from app import timeshift
from app import tuner_manager
from app import scheduler_service
from app import guide_service
from app import cleanup_service
from app.recorder import Recorder

import subprocess
import shutil


app = Flask(__name__)
recorder = Recorder()


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


for folder in [
    config.RECORDINGS,
    config.LOGS,
    config.LIVEBUFFER,
    config.THUMBNAILS,
    config.GUIDE,
]:
    folder.mkdir(parents=True, exist_ok=True)


database.init_db()


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


@app.route("/record-channel/<guide_number>", methods=["POST"])
def record_channel(guide_number):
    ch = database.get_channel(guide_number)
    if ch:
        recorder.start_from_channel(ch)
    return redirect("/")


@app.route("/play/<path:filename>")
def play(filename):
    return send_from_directory(config.RECORDINGS, filename, as_attachment=False)


@app.route("/recording/play/<path:filename>")
def recording_player(filename):
    return render_template("player.html", filename=filename)


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
    hdhr.import_lineup()
    return redirect("/")


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
    return render_template("channel_guide.html", channel=channel, programs=programs)


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
def clear_history():
    database.clear_old_recording_history()
    return redirect("/history")


@app.route("/search")
def search_page():
    q = request.args.get("q", "").strip()
    results = search.search_programs(q) if q else []
    return render_template("search.html", q=q, results=results)


@app.route("/recordings")
def recordings_page():
    recordings = []
    for r in database.list_recordings():
        item = dict(r)
        item["thumbnail"] = thumbnails.make_thumbnail(item["filename"])
        recordings.append(item)

    return render_template("recordings.html", recordings=recordings)


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
    ok = stream_manager.start_stream(channel)
    return jsonify({"ok": ok, "channel": stream_manager.current_channel()})


@app.route("/live/stop", methods=["POST"])
def live_stop():
    stream_manager.stop_stream()
    return jsonify({"ok": True})


@app.route("/api/live/status")
def api_live_status():
    return jsonify(stream_manager.status())


@app.route("/livebuffer/<path:filename>")
def livebuffer(filename):
    return send_from_directory(config.LIVEBUFFER, filename, as_attachment=False)


@app.route("/api/guide")
def api_guide():
    return jsonify(database.get_now_next())


@app.route("/api/programs")
def api_programs():
    return jsonify(database.get_programs())


@app.route("/api/tuners")
def api_tuners():
    return jsonify(tuner_manager.status())


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


scheduler_service.start_scheduler()
guide_service.start_guide_updater()
cleanup_service.start_cleanup_service()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)