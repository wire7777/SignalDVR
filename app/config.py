import os
from pathlib import Path

from app import database

BASE = Path(os.environ.get("SIGNALDVR_BASE", str(Path.home() / "signaldvr"))).expanduser()

_DEFAULT_RECORDINGS = Path(os.environ.get("SIGNALDVR_RECORDINGS", str(BASE / "recordings"))).expanduser()
_DEFAULT_LIVEBUFFER = Path(os.environ.get("SIGNALDVR_TIMESHIFT", str(BASE / "livebuffer"))).expanduser()
_DEFAULT_THUMBNAILS = Path(os.environ.get("SIGNALDVR_THUMBNAILS", str(BASE / "thumbnails"))).expanduser()


def _setting(key):
    try:
        value = database.get_setting(key, "")
        return str(value or "").strip()
    except Exception:
        # Table/DB not initialized yet (e.g. very first run before
        # database.init_db() has created the settings table). Fall back to
        # defaults rather than crashing every module that imports config.
        return ""


def _writable_dir(path):
    """Return True if `path` exists (or can be created) and is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except Exception:
        return False


def _resolved_storage_path(setting_key, default_path):
    """
    Resolve a Settings-page storage folder.

    Falls back to the built-in default (and logs why) if the setting is
    blank, or if the configured path can't be created/written to - a typo'd
    or unmounted path should never prevent SignalDVR from starting.
    """
    raw = _setting(setting_key)

    if not raw:
        default_path.mkdir(parents=True, exist_ok=True)
        return default_path

    custom_path = Path(raw).expanduser()

    if _writable_dir(custom_path):
        return custom_path

    print(
        f"config: '{setting_key}' is set to '{raw}' but that path could not "
        f"be created/written to - falling back to default: {default_path}",
        flush=True,
    )
    default_path.mkdir(parents=True, exist_ok=True)
    return default_path


# NOTE: these are resolved once, at process start. Changing the Recording /
# Timeshift / Thumbnail folder in Settings takes effect after restarting
# SignalDVR - existing files are NOT moved automatically. See the Storage
# section of the Settings page for details.
RECORDINGS = _resolved_storage_path("recording_path", _DEFAULT_RECORDINGS)
LIVEBUFFER = _resolved_storage_path("timeshift_path", _DEFAULT_LIVEBUFFER)
LOGS = Path(os.environ.get("SIGNALDVR_LOGS", str(BASE / "logs"))).expanduser()
THUMBNAILS = _resolved_storage_path("thumbnail_path", _DEFAULT_THUMBNAILS)
GUIDE = Path(os.environ.get("SIGNALDVR_GUIDE", str(BASE / "guide"))).expanduser()
GUIDE_XML = GUIDE / "guide.xml"
GUIDE_URL = (
    os.environ.get("SIGNALDVR_GUIDE_URL", "").strip()
    or _setting("guide_url")
    or "http://192.168.2.13:8089/api/xmltv"
)

HDHR_DEVICE = (
    os.environ.get("SIGNALDVR_HDHR_DEVICE", "").strip()
    or _setting("hdhr_device")
    or _setting("hdhr_device_id")
    or "1077144F"
)

START_PADDING_SECONDS = 120
STOP_PADDING_SECONDS = 300
PORT = int(os.environ.get("SIGNALDVR_PORT", "8088"))