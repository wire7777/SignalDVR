import subprocess
from pathlib import Path
from app import config


def make_thumbnail(video_filename):
    video_path = config.RECORDINGS / video_filename
    thumb_name = Path(video_filename).with_suffix(".jpg").name
    thumb_path = config.THUMBNAILS / thumb_name

    if not video_path.exists():
        return None

    if thumb_path.exists():
        return thumb_name

    config.THUMBNAILS.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-ss", "00:01:00",
        "-i", str(video_path),
        "-vframes", "1",
        str(thumb_path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if thumb_path.exists():
        return thumb_name

    return None
