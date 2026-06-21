from flask import Flask, send_from_directory, redirect, render_template
from app import config
from app.recorder import Recorder
from app import database
import subprocess
import shutil
from pathlib import Path
from app import hdhr

app = Flask(__name__)

recorder = Recorder()

for folder in [config.RECORDINGS, config.LOGS, config.LIVEBUFFER, config.THUMBNAILS]:
    folder.mkdir(parents=True, exist_ok=True)

database.init_db()


@app.route("/")
def index():
    recordings = database.list_recordings()
    channels = database.list_channels()
    status = "RECORDING" if recorder.is_recording() else "IDLE"
    return render_template("index.html", recordings=recordings, channels=channels, status=status)

    html = f"""
    <h1>AndresDVR v0.1</h1>
    <h2>Status: {status}</h2>

    <form action="/start" method="post">
        <button type="submit">Start Recording 17.1</button>
    </form>

    <form action="/stop" method="post">
        <button type="submit">Stop Recording</button>
    </form>

    <h2>Recordings</h2>
    <ul>
    """

    for r in recordings:
        size_mb = (r["size_bytes"] or 0) / 1024 / 1024
        html += f"""
        <li>
            <b>{r["title"]}</b> - {r["channel"]} - {r["status"]}
            - {size_mb:.1f} MB
            - <a href="/play/{r["filename"]}">Play/Download</a>
<form action="/delete/{r["filename"]}" method="post" style="display:inline;">
    <button type="submit">Delete</button>
</form>
        </li>
        """

    html += "</ul>"
    return html

@app.route("/scan-channels", methods=["POST"])
def scan_channels():
    hdhr.import_lineup()
    return redirect("/")




@app.route("/start", methods=["POST"])
def start():
    recorder.start()
    return redirect("/")


@app.route("/stop", methods=["POST"])
def stop():
    recorder.stop()
    return redirect("/")


@app.route("/play/<filename>")
def play(filename):
    return send_from_directory(config.RECORDINGS, filename, as_attachment=False)



@app.route("/record-channel/<guide_number>", methods=["POST"])
def record_channel(guide_number):
    ch = database.get_channel(guide_number)
    if ch:
        recorder.start_from_channel(ch)
    return redirect("/")




@app.route("/health")
def health():
    disk = shutil.disk_usage(config.RECORDINGS)

    try:
        hdhr = subprocess.check_output(
            ["hdhomerun_config", config.HDHR_DEVICE, "get", "/tuner1/debug"],
            text=True
        )
    except Exception as e:
        hdhr = f"HDHomeRun error: {e}"

    html = f"""
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
    <pre>{hdhr}</pre>
    """
    return html

@app.route("/delete/<filename>", methods=["POST"])
def delete_recording(filename):
    path = config.RECORDINGS / filename
    if path.exists():
        path.unlink()

    database.delete_recording(filename)
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)
