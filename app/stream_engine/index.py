import json
from pathlib import Path


INDEX_FILE = "segments.json"


def index_path(session_dir):
    return Path(session_dir) / INDEX_FILE


def load_index(session_dir):
    path = index_path(session_dir)

    if not path.exists():
        return []

    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def save_index(session_dir, rows):
    path = index_path(session_dir)
    path.write_text(json.dumps(rows, indent=2))


def rebuild_index_from_files(session_dir):
    session_dir = Path(session_dir)
    segments = sorted(session_dir.glob("segment_*.ts"))

    rows = []

    for i, seg in enumerate(segments):
        rows.append({
            "sequence": i,
            "file": seg.name,
            "size": seg.stat().st_size,
        })

    save_index(session_dir, rows)
    return rows


def latest_segments(session_dir, count):
    rows = load_index(session_dir)

    if not rows:
        rows = rebuild_index_from_files(session_dir)

    return rows[-count:]
