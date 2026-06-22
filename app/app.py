from flask import Flask, send_from_directory, redirect, render_template, jsonify, request

from app import config
from app.recorder import Recorder
from app import stream_manager
from app import database
from app import hdhr
from app import epg
from app import search
from app import thumbnails
from app import timeshift

import subprocess
import shutil


app = Flask(__name__)
recorder = Recorder()


@app.template_filter("category_color")
def category_color(category):
    colors = {
        "Sports": "#0066cc",
        "News": "#cc0000",
        "Movie": "#7b1fa2",
        "Comedy": "#ff9800",
        "Kids": "#43a047",
        "Drama": "#5c6bc0",
        "Reality": "#8d6e63",
        "Documentary": "#009688",
    }
    return colors.get(category or "", "#333333")


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


@app.route("/delete/<path:filename>", methods=["GET", "POST"])
def delete_recording(filename):
    path = config.RECORDINGS / filename
    if path.exists():
        path.unlink()
    database.delete_recording(filename)
    return redirect("/")


@app.route("/scan-channels", methods=["POST"])
def scan_channels():
    hdhr.import_lineup()
    return redirect("/")


@app.route("/import-epg", methods=["POST"])
def import_epg():
    epg.update_guide()
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
    grid = database.get_guide_grid()
    return render_template("grid.html", grid=grid)


@app.route("/scheduled")
def scheduled_page():
    scheduled = database.list_scheduled_recordings()
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
        priority=50,
    )
    return redirect("/series")


@app.route("/series/apply", methods=["POST"])
def apply_series():
    database.apply_series_rules()
    return redirect("/scheduled")


@app.route("/series/priority/<int:series_id>/<int:priority>", methods=["POST"])
def set_series_priority(series_id, priority):
    database.set_series_priority(series_id, priority)
    return redirect("/series")


@app.route("/series/delete/<int:series_id>", methods=["POST"])
def delete_series(series_id):
    database.delete_series_recording(series_id)
    return redirect("/series")


@app.route("/conflicts")
def conflicts_page():
    conflicts = database.get_schedule_conflicts()
    return render_template("conflicts.html", conflicts=conflicts)


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

@app.route("/api/timeshift/status")
def api_timeshift_status():
    return jsonify(timeshift.status())

@app.route("/api/timeshift/replay/<int:seconds>", methods=["POST"])
def api_timeshift_replay(seconds):
    timeshift.seek_relative(-seconds)
    return jsonify(timeshift.status())


@app.route("/api/timeshift/skip/<int:seconds>", methods=["POST"])
def api_timeshift_skip(seconds):
    timeshift.seek_relative(seconds)
    return jsonify(timeshift.status())

@app.route("/timeshift")
def timeshift_page():
    channels = database.list_channels()
    return render_template(
        "timeshift.html",
        channels=channels,
        status=timeshift.status(),
    )


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


@app.route("/api/timeshift/seek/<int:seconds>", methods=["POST"])
def api_timeshift_seek(seconds):
    timeshift.seek_relative(seconds)
    return jsonify(timeshift.status())


@app.route("/api/timeshift/live", methods=["POST"])
def api_timeshift_live():
    timeshift.jump_to_live()
    return jsonify(timeshift.status())

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


@app.route("/health")
def health():
    disk = shutil.disk_usage(config.RECORDINGS)

    try:
        tuner = subprocess.check_output(
            ["hdhomerun_config", config.HDHR_DEVICE, "get", "/tuner1/debug"],
            text=True,
        )
    except Exception as e:
        tuner = f"HDHomeRun error: {e}"

    return f"""
<h1>AndresDVR Health</h1>
<p><a href="/">Back</a></p>

<h2>Recorder</h2>
<pre>Status: {"RECORDING" if recorder.is_recording() else "IDLE"}</pre>

<h2>Disk</h2>
<pre>
Total: {disk.total / 1024 / 1024 / 1024:.1f} GB
Used:  {disk.used / 1024 / 1024 / 1024:.1f} GB
Free:  {disk.free / 1024 / 1024 / 1024:.1f} GB
</pre>

<h2>HDHomeRun</h2>
<pre>{tuner}</pre>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)