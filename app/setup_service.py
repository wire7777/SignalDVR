import ipaddress
import re
import subprocess
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import config, database

SETUP_KEY = "setup_complete"

_DISCOVERY_RE = re.compile(
    r"hdhomerun device\s+(?P<device>[0-9A-Fa-f]+)\s+found at\s+(?P<ip>[^\s]+)",
    re.IGNORECASE,
)


def is_complete():
    return str(database.get_setting(SETUP_KEY, "0")).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def storage_status():
    result = []

    for name, path in (
        ("Recordings", config.RECORDINGS),
        ("Timeshift", config.LIVEBUFFER),
        ("Thumbnails", config.THUMBNAILS),
        ("Guide", config.GUIDE),
        ("Logs", config.LOGS),
    ):
        directory = Path(path)
        ok = False
        error = ""

        try:
            directory.mkdir(parents=True, exist_ok=True)

            test_file = directory / ".signaldvr-write-test"
            test_file.write_text("ok")
            test_file.unlink()

            ok = True
        except Exception as exc:
            error = str(exc)

        result.append(
            {
                "name": name,
                "path": str(directory),
                "ok": ok,
                "error": error,
            }
        )

    return result


def _is_ipv4_address(value):
    """Return True only for a valid IPv4 address."""
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


def _read_hdhr_model(device_id, timeout=4):
    """Read the HDHomeRun model name without failing discovery."""
    try:
        result = subprocess.run(
            ["hdhomerun_config", device_id, "get", "/sys/model"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def discover_hdhr_devices(timeout=8):
    """
    Discover HDHomeRun tuners using IPv4 only.

    hdhomerun_config may return both an IPv6 link-local address and an IPv4
    address for the same tuner. SignalDVR intentionally ignores every IPv6
    result and returns only IPv4 tuners.
    """
    try:
        completed = subprocess.run(
            ["hdhomerun_config", "discover"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return [], "hdhomerun_config is not installed."
    except subprocess.TimeoutExpired:
        return [], "HDHomeRun discovery timed out."
    except Exception as exc:
        return [], f"HDHomeRun discovery failed: {exc}"

    output = "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    ).strip()

    devices_by_id = {}

    for match in _DISCOVERY_RE.finditer(output):
        device_id = match.group("device").upper()
        ip_address = match.group("ip").strip()

        # Never use IPv6, including link-local fe80:: addresses.
        if not _is_ipv4_address(ip_address):
            continue

        # Keep one IPv4 entry per physical tuner.
        devices_by_id[device_id] = ip_address

    devices = []

    for device_id, ip_address in devices_by_id.items():
        model = _read_hdhr_model(device_id)

        devices.append(
            {
                "device_id": device_id,
                "ip": ip_address,
                "model": model,
                "label": (
                    f"{device_id} — {ip_address}"
                    + (f" — {model}" if model else "")
                ),
            }
        )

    devices.sort(key=lambda item: item["device_id"])

    if devices:
        return devices, ""

    if output:
        return [], (
            "No IPv4 HDHomeRun tuners were discovered. "
            "IPv6 tuner addresses are intentionally ignored. "
            "You can enter an IPv4 address or device ID manually."
        )

    return [], (
        "No IPv4 HDHomeRun tuners were discovered. "
        "You can enter an IPv4 address or device ID manually."
    )


def finish(form):
    username = (form.get("username") or "admin").strip() or "admin"
    password = form.get("password") or ""

    if len(password) < 8:
        raise ValueError("Administrator password must be at least 8 characters.")

    selected_device = (form.get("hdhr_device") or "").strip()
    manual_device = (form.get("hdhr_device_manual") or "").strip()
    hdhr_device = manual_device or selected_device

    selected_host = (form.get("hdhr_host") or "").strip()

    if not hdhr_device:
        raise ValueError(
            "Select a discovered HDHomeRun tuner or enter its device ID/IP manually."
        )

    # Never save an IPv6 tuner address.
    if ":" in hdhr_device:
        raise ValueError(
            "IPv6 tuner addresses are not supported. "
            "Enter the HDHomeRun device ID or its IPv4 address."
        )

    if selected_host and not _is_ipv4_address(selected_host):
        raise ValueError(
            "The selected HDHomeRun host must be a valid IPv4 address."
        )

    # If the manual value itself is IPv4, use it as both device and host.
    if _is_ipv4_address(hdhr_device):
        hdhr_host = hdhr_device
    else:
        hdhr_host = selected_host

    settings = {
        "admin_username": username,
        "admin_password_hash": generate_password_hash(password),
        "hdhr_device": hdhr_device,
        "hdhr_device_id": hdhr_device if not _is_ipv4_address(hdhr_device) else "",
        "hdhr_host": hdhr_host,
        "hdhr_ip": hdhr_host,
        "guide_source": (
            form.get("guide_source") or "schedules_direct"
        ).strip(),
        "guide_days": (form.get("guide_days") or "15").strip(),
        "background_dvr_retention": (
            form.get("background_dvr_retention") or "3_days"
        ).strip(),
        "max_recordings": (form.get("max_recordings") or "4").strip(),
        SETUP_KEY: "1",
    }

    for key, value in settings.items():
        database.set_setting(key, value)