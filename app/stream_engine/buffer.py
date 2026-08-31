from pathlib import Path


def segment_files(session_dir):
    return sorted(Path(session_dir).glob("segment_*.ts"))


def cleanup_segments(session_dir, keep_segments=900):
    segments = segment_files(session_dir)

    if len(segments) <= keep_segments:
        return 0

    old_segments = segments[:len(segments) - keep_segments]
    removed = 0

    for seg in old_segments:
        try:
            seg.unlink()
            removed += 1
        except Exception:
            pass

    return removed


def segment_count(session_dir):
    return len(segment_files(session_dir))


def buffer_seconds(session_dir, segment_seconds=4):
    return segment_count(session_dir) * segment_seconds
