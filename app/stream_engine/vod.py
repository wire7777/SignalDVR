import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app import config
from app import playback_index


class VodError(RuntimeError):
    pass


VALID_PROFILES = {"vlc", "media3", "browser"}

_BROWSER_JOBS: dict[str, subprocess.Popen] = {}
_BROWSER_LOCK = threading.Lock()


def _recording_path(filename: str) -> Path:
    base = config.RECORDINGS.resolve()
    path = (base / filename).resolve()

    if path != base and base not in path.parents:
        raise VodError("Invalid recording path")

    if not path.exists() or not path.is_file():
        raise VodError(f"Recording not found: {filename}")

    return path


def _safe_key(filename: str) -> str:
    stem = Path(filename).stem
    readable = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in stem
    ).strip("_")[:70]

    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]
    return f"{readable}_{digest}" if readable else digest


def vod_directory(filename: str, profile: str = "vlc") -> Path:
    profile = profile.strip().lower()

    if profile not in VALID_PROFILES:
        raise VodError(f"Invalid VOD profile: {profile}")

    return config.RECORDINGS / ".vod" / _safe_key(filename) / profile


def playlist_path(filename: str, profile: str = "vlc") -> Path:
    return vod_directory(filename, profile) / "index.m3u8"


def metadata_path(filename: str, profile: str = "vlc") -> Path:
    return vod_directory(filename, profile) / "vod.json"


def playlist_url(filename: str, profile: str = "vlc") -> str:
    directory = vod_directory(filename, profile)
    relative = directory.relative_to(config.RECORDINGS)

    return f"/play/{relative.as_posix()}/index.m3u8"


def _source_signature(path: Path) -> dict[str, int]:
    stat = path.stat()

    return {
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_current(
    filename: str,
    profile: str,
    source_path: Path,
) -> bool:
    playlist = playlist_path(filename, profile)
    metadata = metadata_path(filename, profile)

    if not playlist.exists() or playlist.stat().st_size <= 0:
        return False

    if not metadata.exists():
        return False

    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except Exception:
        return False

    if (
        data.get("profile") != profile
        or data.get("source") != _source_signature(source_path)
    ):
        return False

    if data.get("status") == "building":
        return _process_running(int(data.get("pid") or 0))

    return data.get("status", "complete") == "complete"


def _browser_ready(output: Path) -> bool:
    playlist = output / "index.m3u8"

    if not playlist.exists() or playlist.stat().st_size <= 0:
        return False

    return len(list(output.glob("segment_*.ts"))) >= 2


def _read_log_tail(path: Path, characters: int = 4000) -> str:
    try:
        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )[-characters:]
    except Exception:
        return ""


def _finish_browser_job(
    key: str,
    process: subprocess.Popen,
    log_handle,
    output: Path,
    metadata_file: Path,
    metadata: dict[str, Any],
) -> None:
    try:
        try:
            return_code = process.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            process.terminate()

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

            return_code = -1

        segment_count = len(list(output.glob("segment_*.ts")))

        metadata["segment_count"] = segment_count
        metadata["return_code"] = return_code
        metadata["finished_at"] = time.time()

        if return_code == 0 and (output / "index.m3u8").exists():
            metadata["status"] = "complete"
        else:
            metadata["status"] = "failed"
            metadata["error"] = _read_log_tail(output / "ffmpeg.log")

        metadata_file.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

    finally:
        try:
            log_handle.close()
        except Exception:
            pass

        with _BROWSER_LOCK:
            current = _BROWSER_JOBS.get(key)

            if current is process:
                _BROWSER_JOBS.pop(key, None)


def _build_browser_vod(
    filename: str,
    source: Path,
    force: bool,
) -> dict[str, Any]:
    profile = "browser"
    output = vod_directory(filename, profile)
    playlist = playlist_path(filename, profile)
    metadata_file = metadata_path(filename, profile)
    key = str(output)

    if not force and _is_current(filename, profile, source):
        return {
            "ok": True,
            "cached": True,
            "building": False,
            "filename": filename,
            "profile": profile,
            "duration_seconds": 0,
            "playlist_path": str(playlist),
            "playlist_url": playlist_url(filename, profile),
        }

    output.parent.mkdir(parents=True, exist_ok=True)

    with _BROWSER_LOCK:
        process = _BROWSER_JOBS.get(key)

        if process is not None and process.poll() is not None:
            _BROWSER_JOBS.pop(key, None)
            process = None

        if process is None:
            if output.exists():
                shutil.rmtree(output)

            output.mkdir(parents=True, exist_ok=True)

            segment_pattern = output / "segment_%06d.ts"
            log_path = output / "ffmpeg.log"

            command = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-i", str(source),

                "-map", "0:v:0",
                "-map", "0:a:0",

                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",

                "-g", "60",
                "-keyint_min", "60",
                "-sc_threshold", "0",

                "-c:a", "aac",
                "-b:a", "160k",
                "-ac", "2",

                "-f", "hls",
                "-hls_playlist_type", "event",
                "-hls_time", "2",
                "-hls_list_size", "0",
                "-hls_flags", "independent_segments",
                "-hls_segment_filename", str(segment_pattern),
                str(playlist),
            ]

            log_handle = log_path.open("w", encoding="utf-8")
            log_handle.write("FFMPEG COMMAND:\n")
            log_handle.write(" ".join(command))
            log_handle.write("\n\n")
            log_handle.flush()

            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

            metadata = {
                "version": 2,
                "filename": filename,
                "profile": profile,
                "status": "building",
                "pid": process.pid,
                "started_at": time.time(),
                "source": _source_signature(source),
                "ffmpeg_command": command,
            }

            metadata_file.write_text(
                json.dumps(metadata, indent=2) + "\n",
                encoding="utf-8",
            )

            _BROWSER_JOBS[key] = process

            monitor = threading.Thread(
                target=_finish_browser_job,
                args=(
                    key,
                    process,
                    log_handle,
                    output,
                    metadata_file,
                    metadata,
                ),
                daemon=True,
                name=f"browser-vod-{_safe_key(filename)}",
            )
            monitor.start()

    deadline = time.time() + 45

    while time.time() < deadline:
        if _browser_ready(output):
            return {
                "ok": True,
                "cached": False,
                "building": True,
                "filename": filename,
                "profile": profile,
                "duration_seconds": 0,
                "playlist_path": str(playlist),
                "playlist_url": playlist_url(filename, profile),
            }

        if process.poll() is not None:
            error = _read_log_tail(output / "ffmpeg.log")
            raise VodError(
                error or "Browser video conversion stopped unexpectedly"
            )

        time.sleep(0.5)

    raise VodError(
        "Browser video did not become playable within 45 seconds. "
        f"Check {output / 'ffmpeg.log'}"
    )


def build_vod(
    filename: str,
    profile: str = "vlc",
    force: bool = False,
) -> dict[str, Any]:
    profile = profile.strip().lower()

    if profile not in VALID_PROFILES:
        raise VodError(f"Invalid VOD profile: {profile}")

    source = _recording_path(filename)

    if profile == "browser":
        return _build_browser_vod(
            filename=filename,
            source=source,
            force=force,
        )

    output = vod_directory(filename, profile)
    playlist = playlist_path(filename, profile)

    if not force and _is_current(filename, profile, source):
        index_data = playback_index.get_or_build_index(filename)

        return {
            "ok": True,
            "cached": True,
            "filename": filename,
            "profile": profile,
            "duration_seconds": index_data.get("duration_seconds", 0),
            "playlist_path": str(playlist),
            "playlist_url": playlist_url(filename, profile),
        }

    output.parent.mkdir(parents=True, exist_ok=True)

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f"{profile}_",
            dir=output.parent,
        )
    )

    temporary_playlist = temporary / "index.m3u8"
    segment_pattern = temporary / "segment_%06d.ts"

    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "0:a:0",
    ]

    if profile == "media3":
        command += [
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",
        ]
    else:
        command += [
            "-c:v", "copy",
            "-c:a", "copy",
        ]

    command += [
        "-f", "hls",
        "-hls_playlist_type", "vod",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(segment_pattern),
        str(temporary_playlist),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise VodError("VOD generation timed out") from exc

    if result.returncode != 0 or not temporary_playlist.exists():
        error = result.stderr[-4000:] or "FFmpeg VOD generation failed"
        shutil.rmtree(temporary, ignore_errors=True)
        raise VodError(error)

    index_data = playback_index.get_or_build_index(filename)
    segment_count = len(list(temporary.glob("segment_*.ts")))

    metadata = {
        "version": 1,
        "filename": filename,
        "profile": profile,
        "status": "complete",
        "duration_seconds": index_data.get("duration_seconds", 0),
        "segment_count": segment_count,
        "source": _source_signature(source),
        "ffmpeg_command": command,
    }

    (temporary / "vod.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    if output.exists():
        shutil.rmtree(output)

    temporary.replace(output)

    return {
        "ok": True,
        "cached": False,
        "filename": filename,
        "profile": profile,
        "duration_seconds": index_data.get("duration_seconds", 0),
        "segment_count": segment_count,
        "playlist_path": str(playlist_path(filename, profile)),
        "playlist_url": playlist_url(filename, profile),
    }


def delete_vod(filename: str) -> bool:
    root = config.RECORDINGS / ".vod" / _safe_key(filename)

    if not root.exists():
        return False

    shutil.rmtree(root)
    return True

# ---------------------------------------------------------------------------
# Program Catalog browser VOD
# ---------------------------------------------------------------------------

_PROGRAM_BROWSER_JOBS: dict[str, subprocess.Popen] = {}
_PROGRAM_BROWSER_LOCK = threading.Lock()


def _safe_program_cache_key(source_playlist: Path) -> str:
    resolved = source_playlist.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    readable = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in resolved.parent.name
    ).strip("_")[:60]

    return f"{readable}_{digest}" if readable else digest


def program_browser_vod_directory(source_playlist: Path) -> Path:
    """
    Store browser-compatible Program Catalog VOD outside the source program
    folder so the original Background DVR HLS files remain untouched.
    """
    key = _safe_program_cache_key(source_playlist)
    return Path(config.LIVEBUFFER) / ".program_vod" / key / "browser"


def _program_source_signature(source_playlist: Path) -> dict[str, Any]:
    playlist_stat = source_playlist.stat()

    segment_stats = []
    for segment in sorted(source_playlist.parent.glob("*.ts")):
        try:
            stat = segment.stat()
            segment_stats.append((segment.name, stat.st_size, stat.st_mtime_ns))
        except OSError:
            continue

    return {
        "playlist_size": playlist_stat.st_size,
        "playlist_modified_ns": playlist_stat.st_mtime_ns,
        "segment_count": len(segment_stats),
        "segments_digest": hashlib.sha256(
            repr(segment_stats).encode("utf-8")
        ).hexdigest(),
    }


def _program_browser_current(
    source_playlist: Path,
    output: Path,
) -> bool:
    playlist = output / "index.m3u8"
    metadata_file = output / "vod.json"

    if not playlist.exists() or playlist.stat().st_size <= 0:
        return False

    if not metadata_file.exists():
        return False

    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except Exception:
        return False

    if metadata.get("source") != _program_source_signature(source_playlist):
        return False

    if metadata.get("status") == "building":
        return _process_running(int(metadata.get("pid") or 0))

    return metadata.get("status") == "complete"


def _finish_program_browser_job(
    key: str,
    process: subprocess.Popen,
    log_handle,
    output: Path,
    metadata_file: Path,
    metadata: dict[str, Any],
) -> None:
    try:
        try:
            return_code = process.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            process.terminate()

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

            return_code = -1

        metadata["return_code"] = return_code
        metadata["finished_at"] = time.time()
        metadata["segment_count"] = len(list(output.glob("segment_*.ts")))

        if return_code == 0 and (output / "index.m3u8").exists():
            metadata["status"] = "complete"
        else:
            metadata["status"] = "failed"
            metadata["error"] = _read_log_tail(output / "ffmpeg.log")

        metadata_file.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        try:
            log_handle.close()
        except Exception:
            pass

        with _PROGRAM_BROWSER_LOCK:
            current = _PROGRAM_BROWSER_JOBS.get(key)
            if current is process:
                _PROGRAM_BROWSER_JOBS.pop(key, None)


def build_program_browser_vod(
    source_playlist: Path,
    force: bool = False,
) -> dict[str, Any]:
    """
    Convert a Program Catalog HLS playlist containing native OTA MPEG-2 video
    into browser-safe H.264/AAC HLS.

    The function starts FFmpeg once, waits only until at least two segments are
    available, and then lets conversion continue in the background.
    """
    source_playlist = Path(source_playlist).resolve()

    if not source_playlist.exists() or not source_playlist.is_file():
        raise VodError(f"Program playlist not found: {source_playlist}")

    if source_playlist.suffix.lower() != ".m3u8":
        raise VodError("Program source must be an HLS playlist")

    output = program_browser_vod_directory(source_playlist)
    playlist = output / "index.m3u8"
    metadata_file = output / "vod.json"
    key = str(output)

    if not force and _program_browser_current(source_playlist, output):
        return {
            "ok": True,
            "cached": True,
            "building": False,
            "playlist_path": str(playlist),
            "output_directory": str(output),
        }

    output.parent.mkdir(parents=True, exist_ok=True)

    with _PROGRAM_BROWSER_LOCK:
        process = _PROGRAM_BROWSER_JOBS.get(key)

        if process is not None and process.poll() is not None:
            _PROGRAM_BROWSER_JOBS.pop(key, None)
            process = None

        if process is None:
            if output.exists():
                shutil.rmtree(output)

            output.mkdir(parents=True, exist_ok=True)

            segment_pattern = output / "segment_%06d.ts"
            log_path = output / "ffmpeg.log"

            command = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-allowed_extensions", "ALL",
                "-i", str(source_playlist),

                "-map", "0:v:0",
                "-map", "0:a:0",

                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",

                "-g", "60",
                "-keyint_min", "60",
                "-sc_threshold", "0",

                "-c:a", "aac",
                "-b:a", "160k",
                "-ac", "2",

                "-f", "hls",
                "-hls_playlist_type", "event",
                "-hls_time", "2",
                "-hls_list_size", "0",
                "-hls_flags", "independent_segments",
                "-hls_segment_filename", str(segment_pattern),
                str(playlist),
            ]

            log_handle = log_path.open("w", encoding="utf-8")
            log_handle.write("FFMPEG COMMAND:\n")
            log_handle.write(" ".join(command))
            log_handle.write("\n\n")
            log_handle.flush()

            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

            metadata = {
                "version": 1,
                "profile": "browser",
                "status": "building",
                "pid": process.pid,
                "started_at": time.time(),
                "source_playlist": str(source_playlist),
                "source": _program_source_signature(source_playlist),
                "ffmpeg_command": command,
            }

            metadata_file.write_text(
                json.dumps(metadata, indent=2) + "\n",
                encoding="utf-8",
            )

            _PROGRAM_BROWSER_JOBS[key] = process

            monitor = threading.Thread(
                target=_finish_program_browser_job,
                args=(
                    key,
                    process,
                    log_handle,
                    output,
                    metadata_file,
                    metadata,
                ),
                daemon=True,
                name=f"program-browser-vod-{_safe_program_cache_key(source_playlist)}",
            )
            monitor.start()

    deadline = time.time() + 45

    while time.time() < deadline:
        if _browser_ready(output):
            return {
                "ok": True,
                "cached": False,
                "building": process.poll() is None,
                "playlist_path": str(playlist),
                "output_directory": str(output),
            }

        if process.poll() is not None:
            error = _read_log_tail(output / "ffmpeg.log")
            raise VodError(
                error or "Program browser conversion stopped unexpectedly"
            )

        time.sleep(0.5)

    raise VodError(
        "Program browser video did not become playable within 45 seconds. "
        f"Check {output / 'ffmpeg.log'}"
    )