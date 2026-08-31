import re
import subprocess
import threading
import time
from datetime import datetime

from app import config
from app import database
from app import native_dvb


_lock = threading.Lock()

# tuner_id -> job dict, or None if idle. Sized dynamically from Settings
# (see _configured_total) rather than a fixed constant, since the total
# number of physical tuners now depends on which source(s) are active.
_tuners = {}

# HDHomeRun hardware status cache. The web page refreshes once per second,
# but all clients share this cache so multiple tabs do not multiply hardware
# polling.
_hardware_cache_lock = threading.Lock()
_hardware_cache = {
    "updated_monotonic": 0.0,
    "payload": None,
}
_HARDWARE_CACHE_SECONDS = 0.85


def _source_enabled(source):
    source = str(source or "").strip().lower()

    setting = {
        "hdhomerun": "hdhomerun_enabled",
        "native_dvb": "native_dvb_enabled",
    }.get(source)

    if not setting:
        return False

    default = "1" if source == "hdhomerun" else "0"

    return str(
        database.get_setting(setting, default)
        or default
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _int_setting(key, default):
    try:
        return max(1, int(database.get_setting(key, str(default)) or default))
    except Exception:
        return default


def _source_max(source):
    source = str(source or "").strip().lower()

    if source == "native_dvb":
        try:
            return len(native_dvb.discover_adapters())
        except Exception:
            return 0

    if source == "hdhomerun":
        return _int_setting("hdhr_max_tuners", 4)

    return 0

def _configured_total():
    total = 0

    if _source_enabled("hdhomerun"):
        total += _source_max("hdhomerun")

    if _source_enabled("native_dvb"):
        total += _source_max("native_dvb")

    return total

def _configured_mode():
    hdhr = _source_enabled("hdhomerun")
    native = _source_enabled("native_dvb")

    if hdhr and native:
        return "hdhomerun+native_dvb"

    if native:
        return "native_dvb"

    if hdhr:
        return "hdhomerun"

    return "disabled"

def _ensure_slots():
    """Grow/shrink the tuner pool to match current Settings. Never removes
    a slot that's currently busy, even if the configured total shrank."""
    total = _configured_total()

    for tuner_id in range(total):
        _tuners.setdefault(tuner_id, None)

    for tuner_id in list(_tuners.keys()):
        if tuner_id >= total and _tuners.get(tuner_id) is None:
            del _tuners[tuner_id]


def _source_slot_ids(source):
    source = str(source or "").strip().lower()

    hdhr_count = (
        _source_max("hdhomerun")
        if _source_enabled("hdhomerun")
        else 0
    )

    native_count = (
        _source_max("native_dvb")
        if _source_enabled("native_dvb")
        else 0
    )

    if source == "hdhomerun":
        return list(range(0, hdhr_count))

    if source == "native_dvb":
        return list(
            range(
                hdhr_count,
                hdhr_count + native_count,
            )
        )

    return list(range(_configured_total()))


def allocate(
    channel,
    purpose="recording",
    title="",
    source="",
):
    source = str(source or "").strip().lower()

    if source and not _source_enabled(source):
        return None

    # Native DVB also has a physical reservation layer. Do not allocate a
    # logical job if every Linux DVB adapter is already owned.
    if source == "native_dvb":
        try:
            if not any(
                not item.get("busy")
                for item in native_dvb.adapter_status()
            ):
                return None
        except Exception:
            return None

    with _lock:
        _ensure_slots()

        slot_ids = _source_slot_ids(source)

        for tuner_id in slot_ids:
            if _tuners.get(tuner_id) is None:
                _tuners[tuner_id] = {
                    "tuner_id": tuner_id,
                    "channel": channel,
                    "purpose": purpose,
                    "title": title,
                    "source": source,
                    "started_at": datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
                return tuner_id

        return None

def release(tuner_id):
    with _lock:
        if tuner_id in _tuners:
            _tuners[tuner_id] = None
            return True

        return False


def release_channel(channel):
    with _lock:
        released = []

        for tuner_id, job in _tuners.items():
            if job and job.get("channel") == channel:
                _tuners[tuner_id] = None
                released.append(tuner_id)

        return released


def status():
    with _lock:
        _ensure_slots()
        result = []

        for tuner_id in sorted(_tuners.keys()):
            job = _tuners[tuner_id]

            if job:
                result.append({
                    "tuner_id": tuner_id,
                    "state": "busy",
                    "channel": job.get("channel"),
                    "purpose": job.get("purpose"),
                    "title": job.get("title"),
                    "source": job.get("source", ""),
                    "started_at": job.get("started_at"),
                })
            else:
                result.append({
                    "tuner_id": tuner_id,
                    "state": "idle",
                    "channel": "",
                    "purpose": "",
                    "title": "",
                    "source": "",
                    "started_at": "",
                })

        return result


def busy_count(source=None):
    with _lock:
        if source:
            return sum(
                1
                for job in _tuners.values()
                if job and job.get("source") == source
            )
        return sum(1 for job in _tuners.values() if job is not None)


def free_count(source=None):
    source = (
        str(source or "").strip().lower()
        if source
        else ""
    )

    if source and not _source_enabled(source):
        return 0

    if source == "native_dvb":
        try:
            return sum(
                1
                for item in native_dvb.adapter_status()
                if not item.get("busy")
            )
        except Exception:
            return 0

    if source == "hdhomerun":
        total = _source_max("hdhomerun")

        try:
            hardware_rows = [
                _hardware_tuner_status(tuner_id)
                for tuner_id in range(total)
            ]

            busy = sum(
                1
                for item in hardware_rows
                if item.get("active")
            )

            return max(
                0,
                total - busy,
            )

        except Exception:
            # Hardware polling failure: fall back to SignalDVR's internal
            # allocation bookkeeping rather than reporting zero tuners.
            return max(
                0,
                total
                - busy_count("hdhomerun"),
            )

    with _lock:
        _ensure_slots()

        return sum(
            1
            for job in _tuners.values()
            if job is None
        )

def idle_count():
    return free_count()


def reset():
    with _lock:
        for tuner_id in _tuners:
            _tuners[tuner_id] = None


def _run_hdhr_get(path, timeout_seconds=1.2):
    """
    Read one HDHomeRun variable through hdhomerun_config.

    Returns an empty string instead of raising so the tuner dashboard remains
    usable when the device is offline or hdhomerun_config is unavailable.
    """
    device = str(getattr(config, "HDHR_DEVICE", "") or "").strip()

    if not device:
        return ""

    try:
        result = subprocess.run(
            [
                "hdhomerun_config",
                device,
                "get",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    return (result.stdout or "").strip()


_STATUS_TOKEN = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")


def _parse_status_line(raw):
    values = {}

    for key, value in _STATUS_TOKEN.findall(raw or ""):
        values[key.lower()] = value

    return values


def _parse_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def _clean_streaminfo_name(value):
    name = str(value or "").strip()

    # HDHomeRun may append status notes such as "(control)" or
    # "(encrypted)" after the friendly channel name.
    name = re.sub(
        r"\s+\((?:control|encrypted|no data|unsupported)\)\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    return name.strip()


def _parse_streaminfo(raw_streaminfo, selected_program):
    """
    Resolve the selected MPEG program to its virtual channel number and name.

    Typical HDHomeRun output contains a line similar to:

        3: 17.1 KGET-HD

    The value from /tunerN/program identifies which streaminfo line is active.
    """
    selected = str(selected_program or "").strip()
    fallback = {
        "program_number": selected,
        "virtual_channel": "",
        "channel_name": "",
    }

    for raw_line in str(raw_streaminfo or "").splitlines():
        line = raw_line.strip()

        if not line or ":" not in line:
            continue

        program_number, description = line.split(":", 1)
        program_number = program_number.strip()

        if selected and program_number != selected:
            continue

        description = description.strip()

        match = re.match(
            r"^(?P<number>\d+(?:\.\d+)?)\s+(?P<name>.+)$",
            description,
        )

        if not match:
            if selected and program_number == selected:
                fallback["program_number"] = program_number
            continue

        return {
            "program_number": program_number,
            "virtual_channel": match.group("number").strip(),
            "channel_name": _clean_streaminfo_name(
                match.group("name")
            ),
        }

    return fallback


def _hardware_tuner_status(tuner_id):
    raw_status = _run_hdhr_get(f"/tuner{tuner_id}/status")
    raw_target = _run_hdhr_get(f"/tuner{tuner_id}/target")

    values = _parse_status_line(raw_status)

    channel = values.get("ch", "none")
    lock = values.get("lock", "none")
    signal_strength = _parse_int(values.get("ss"))
    signal_quality = _parse_int(values.get("snq"))
    symbol_quality = _parse_int(values.get("seq"))
    bitrate_bps = _parse_int(values.get("bps"))
    packets_per_second = _parse_int(values.get("pps"))

    active = (
        channel not in ("", "none")
        or lock not in ("", "none")
        or bool(raw_target and raw_target != "none")
    )

    # streaminfo is only useful while a tuner is active. Avoid the extra
    # hardware queries for idle tuners.
    raw_program = ""
    raw_streaminfo = ""
    stream = {
        "program_number": "",
        "virtual_channel": "",
        "channel_name": "",
    }

    if active:
        raw_program = _run_hdhr_get(f"/tuner{tuner_id}/program")
        raw_streaminfo = _run_hdhr_get(
            f"/tuner{tuner_id}/streaminfo"
        )
        stream = _parse_streaminfo(
            raw_streaminfo,
            raw_program,
        )

    return {
        "tuner_id": tuner_id,
        "available": bool(raw_status),
        "active": active,
        "channel_raw": channel,
        "rf_channel": channel,
        "lock": lock,
        "program_number": stream.get("program_number", ""),
        "virtual_channel": stream.get("virtual_channel", ""),
        "channel_name": stream.get("channel_name", ""),
        "streaminfo": raw_streaminfo,
        "signal_strength": signal_strength,
        "signal_quality": signal_quality,
        "symbol_quality": symbol_quality,
        "bitrate_bps": bitrate_bps,
        "bitrate_mbps": round(bitrate_bps / 1_000_000, 2),
        "packets_per_second": packets_per_second,
        "target": raw_target if raw_target and raw_target != "none" else "",
        "raw_status": raw_status,
    }


def _internal_status_by_tuner():
    return {
        int(item.get("tuner_id", -1)): item
        for item in status()
    }


def _active_hdhomerun_live():
    """Return the active SignalDVR HDHomeRun live session, if any."""
    try:
        from app.stream_engine import manager as stream_engine

        active = stream_engine.get_active() or {}
    except Exception:
        return None

    if not active:
        return None

    tuner_source = str(
        active.get("tuner_source")
        or ""
    ).strip().lower()

    source_type = str(
        active.get("source_type")
        or ""
    ).strip().lower()

    source_url = str(
        active.get("source_url")
        or ""
    ).strip().lower()

    is_hdhr = (
        tuner_source == "hdhomerun"
        or (
            source_type == "url"
            and source_url.startswith(
                ("http://", "https://")
            )
        )
    )

    if not is_hdhr:
        return None

    channel = str(
        active.get("channel")
        or ""
    ).strip()

    if not channel:
        return None

    return {
        "channel": channel,
        "guide_name": str(
            active.get("guide_name")
            or channel
        ).strip(),
        "started_at": str(
            active.get("started_at")
            or ""
        ).strip(),
        "session_id": str(
            active.get("session_id")
            or ""
        ).strip(),
    }


def _channel_lookup():
    lookup = {}

    try:
        rows = database.list_channels()
    except Exception:
        rows = []

    for row in rows:
        try:
            item = dict(row)
        except Exception:
            item = row

        try:
            guide_number = str(
                item.get("guide_number") or ""
            ).strip()
            guide_name = str(
                item.get("guide_name") or ""
            ).strip()
        except AttributeError:
            continue

        if guide_number:
            lookup[guide_number] = guide_name

    return lookup


def _merge_tuner_status(hardware_rows):
    internal_by_id = _internal_status_by_tuner()
    channel_names = _channel_lookup()
    merged = []

    for hardware in hardware_rows:
        tuner_id = int(hardware["tuner_id"])
        internal = internal_by_id.get(tuner_id, {})

        internal_busy = internal.get("state") == "busy"
        hardware_busy = bool(hardware.get("active"))
        state = "busy" if (internal_busy or hardware_busy) else "idle"

        channel_number = str(
            internal.get("channel")
            or hardware.get("virtual_channel")
            or ""
        ).strip()

        channel_name = str(
            channel_names.get(channel_number)
            or hardware.get("channel_name")
            or ""
        ).strip()

        merged.append({
            "tuner_id": tuner_id,
            "state": state,
            "channel": channel_number,
            "channel_name": channel_name,
            "purpose": internal.get("purpose") or "",
            "title": internal.get("title") or "",
            "source": internal.get("source") or "hdhomerun",
            "started_at": internal.get("started_at") or "",
            "internal_busy": internal_busy,
            "hardware_busy": hardware_busy,
            "hardware": hardware,
        })

    # Preserve internally allocated jobs that do not correspond to a
    # physical HDHomeRun tuner row.
    hardware_ids = {int(row["tuner_id"]) for row in hardware_rows}

    for tuner_id, internal in sorted(internal_by_id.items()):
        if tuner_id in hardware_ids:
            continue

        channel_number = str(
            internal.get("channel") or ""
        ).strip()

        merged.append({
            **internal,
            "channel": channel_number,
            "channel_name": channel_names.get(
                channel_number,
                "",
            ),
            "internal_busy": internal.get("state") == "busy",
            "hardware_busy": False,
            "hardware": None,
        })

    return merged



def _native_dvb_owner_channel(owner, channel_names):
    """
    Resolve a Native DVB adapter owner to a virtual channel.

    Supported owner formats include:

        17_4
        17.4
        web-web_<client>_17_4

    This is display-only. It does not change tuner ownership,
    reservation, playback, recording, or adapter allocation.
    """
    owner = str(owner or "").strip()

    if not owner:
        return ""

    # Simple live-session owner.
    direct = owner.replace("_", ".")

    if direct in channel_names:
        return direct

    # Browser sessions include the channel at the end:
    #
    #   web-web_<client-id>_17_4
    #
    # Extract only the trailing major/minor channel pair.
    match = re.search(
        r"(?:^|_)(\d+)_(\d+)$",
        owner,
    )

    if match:
        candidate = (
            f"{match.group(1)}."
            f"{match.group(2)}"
        )

        if candidate in channel_names:
            return candidate

    return ""


def _native_dvb_realtime_payload():
    try:
        physical_rows = native_dvb.adapter_status()
    except Exception as error:
        return {
            "ok": False,
            "connected": False,
            "device": "Linux DVB",
            "source_mode": "native_dvb",
            "updated_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "summary": {
                "total": 0,
                "busy": 0,
                "idle": 0,
                "hardware_active": 0,
                "signaldvr_active": 0,
            },
            "tuners": [],
            "error": str(error),
        }

    channel_names = _channel_lookup()
    internal_jobs = [
        item
        for item in status()
        if str(item.get("source") or "").lower()
        == "native_dvb"
        and item.get("state") == "busy"
    ]

    tuners = []

    for item in physical_rows:
        adapter = int(item.get("adapter", 0))
        busy = bool(item.get("busy"))
        owner = str(item.get("owner") or "")

        channel = ""
        purpose = ""
        title = ""

        # Live StreamSession owners use the session/channel key, e.g. 17_1.
        if busy and owner and not owner.startswith("recording-"):
            candidate = _native_dvb_owner_channel(
                owner,
                channel_names,
            )

            if candidate:
                channel = candidate
                purpose = "live"
                title = channel_names.get(
                    candidate,
                    "",
                )

        # Recording ownership is physical; scheduler's logical job contains
        # the richer channel/title information. Match an unassigned job for
        # display purposes without changing ownership semantics.
        if busy and owner.startswith("recording-"):
            purpose = "recording"

            try:
                schedule_id = int(
                    owner.split("-", 1)[1]
                )
            except Exception:
                schedule_id = None

            if schedule_id is not None:
                try:
                    schedule = database.get_scheduled_recording(
                        schedule_id
                    )
                except Exception:
                    schedule = None

                if schedule:
                    channel = str(
                        schedule.get("channel")
                        or ""
                    )
                    title = str(
                        schedule.get("title")
                        or ""
                    )

        tuners.append({
            "tuner_id": adapter,
            "adapter": adapter,
            "state": "busy" if busy else "idle",
            "channel": channel,
            "channel_name": channel_names.get(
                channel,
                "",
            ),
            "purpose": purpose,
            "title": title,
            "source": "native_dvb",
            "started_at": "",
            "internal_busy": busy,
            "hardware_busy": busy,
            "owner": owner,
            "frontend": item.get("frontend", ""),
            "demux": item.get("demux", ""),
            "dvr": item.get("dvr", ""),
            "hardware": {
                "adapter": adapter,
                "frontend": item.get(
                    "frontend",
                    "",
                ),
                "owner": owner,
                "active": busy,
            },
        })

    busy_count_value = sum(
        1
        for item in tuners
        if item["state"] == "busy"
    )

    return {
        "ok": True,
        "connected": bool(tuners),
        "device": "Linux DVB",
        "source_mode": "native_dvb",
        "updated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "summary": {
            "total": len(tuners),
            "busy": busy_count_value,
            "idle": len(tuners) - busy_count_value,
            "hardware_active": busy_count_value,
            "signaldvr_active": busy_count_value,
        },
        "tuners": tuners,
    }


def _build_realtime_payload():
    mode = _configured_mode()
    channel_names = _channel_lookup()
    tuners = []

    # --------------------------------------------------------
    # HDHomeRun physical tuners
    # --------------------------------------------------------
    if _source_enabled("hdhomerun"):
        hdhr_total = _source_max("hdhomerun")

        hardware_rows = [
            _hardware_tuner_status(tuner_id)
            for tuner_id in range(hdhr_total)
        ]

        internal_by_id = _internal_status_by_tuner()

        # Normal live playback is owned by stream_engine rather than the
        # scheduler's logical _tuners allocator. Match that active session
        # to one physical HDHomeRun tuner by virtual channel.
        active_hdhr = _active_hdhomerun_live()
        active_hdhr_tuner_id = None

        if active_hdhr:
            active_channel = str(
                active_hdhr.get("channel")
                or ""
            ).strip()

            for candidate in hardware_rows:
                if (
                    bool(candidate.get("active"))
                    and str(
                        candidate.get("virtual_channel")
                        or ""
                    ).strip()
                    == active_channel
                ):
                    active_hdhr_tuner_id = int(
                        candidate["tuner_id"]
                    )
                    break

        for hardware in hardware_rows:
            tuner_id = int(hardware["tuner_id"])
            internal = internal_by_id.get(
                tuner_id,
                {},
            )

            logical_busy = (
                internal.get("state") == "busy"
                and internal.get("source")
                == "hdhomerun"
            )

            live_busy = (
                active_hdhr_tuner_id is not None
                and tuner_id == active_hdhr_tuner_id
            )

            internal_busy = (
                logical_busy
                or live_busy
            )

            hardware_busy = bool(
                hardware.get("active")
            )

            channel = str(
                internal.get("channel")
                or hardware.get(
                    "virtual_channel"
                )
                or ""
            ).strip()

            tuners.append({
                "tuner_id": tuner_id,
                "physical_id": tuner_id,
                "source": "hdhomerun",
                "state": (
                    "busy"
                    if internal_busy
                    or hardware_busy
                    else "idle"
                ),
                "channel": channel,
                "channel_name": str(
                    channel_names.get(channel)
                    or hardware.get(
                        "channel_name"
                    )
                    or ""
                ).strip(),
                "purpose": (
                    internal.get("purpose")
                    or (
                        "live"
                        if live_busy
                        else ""
                    )
                ),
                "title": (
                    internal.get("title")
                    or (
                        active_hdhr.get(
                            "guide_name",
                            "",
                        )
                        if live_busy
                        and active_hdhr
                        else ""
                    )
                ),
                "started_at": (
                    internal.get("started_at")
                    or (
                        active_hdhr.get(
                            "started_at",
                            "",
                        )
                        if live_busy
                        and active_hdhr
                        else ""
                    )
                ),
                "internal_busy": internal_busy,
                "hardware_busy": hardware_busy,
                "hardware": hardware,
            })

    # --------------------------------------------------------
    # Native Linux DVB physical adapters
    # --------------------------------------------------------
    if _source_enabled("native_dvb"):
        hdhr_offset = (
            _source_max("hdhomerun")
            if _source_enabled("hdhomerun")
            else 0
        )

        try:
            native_rows = native_dvb.adapter_status()
        except Exception:
            native_rows = []

        for row in native_rows:
            adapter = int(
                row.get("adapter", 0)
            )

            owner = str(
                row.get("owner")
                or ""
            )

            busy = bool(
                row.get("busy")
            )

            channel = ""
            purpose = ""
            title = ""

            if busy and owner:
                if owner.startswith(
                    "recording-"
                ):
                    purpose = "recording"

                    try:
                        schedule_id = int(
                            owner.split(
                                "-",
                                1,
                            )[1]
                        )

                        schedule = (
                            database
                            .get_scheduled_recording(
                                schedule_id
                            )
                        )
                    except Exception:
                        schedule = None

                    if schedule:
                        channel = str(
                            schedule.get(
                                "channel"
                            )
                            or ""
                        )

                        title = str(
                            schedule.get(
                                "title"
                            )
                            or ""
                        )

                else:
                    candidate = _native_dvb_owner_channel(
                        owner,
                        channel_names,
                    )

                    if candidate:
                        channel = candidate
                        purpose = "live"
                        title = channel_names.get(
                            candidate,
                            "",
                        )

            tuners.append({
                "tuner_id": (
                    hdhr_offset + adapter
                ),
                "physical_id": adapter,
                "adapter": adapter,
                "source": "native_dvb",
                "state": (
                    "busy"
                    if busy
                    else "idle"
                ),
                "channel": channel,
                "channel_name": (
                    channel_names.get(
                        channel,
                        "",
                    )
                ),
                "purpose": purpose,
                "title": title,
                "started_at": "",
                "internal_busy": busy,
                "hardware_busy": busy,
                "owner": owner,
                "frontend": row.get(
                    "frontend",
                    "",
                ),
                "demux": row.get(
                    "demux",
                    "",
                ),
                "dvr": row.get(
                    "dvr",
                    "",
                ),
                "hardware": {
                    "adapter": adapter,
                    "frontend": row.get(
                        "frontend",
                        "",
                    ),
                    "owner": owner,
                    "active": busy,
                },
            })

    busy = sum(
        1
        for item in tuners
        if item.get("state") == "busy"
    )

    idle = sum(
        1
        for item in tuners
        if item.get("state") == "idle"
    )

    return {
        "ok": True,
        "connected": bool(tuners),
        "device": (
            "HDHomeRun + Linux DVB"
            if (
                _source_enabled("hdhomerun")
                and _source_enabled(
                    "native_dvb"
                )
            )
            else (
                "Linux DVB"
                if _source_enabled(
                    "native_dvb"
                )
                else str(
                    getattr(
                        config,
                        "HDHR_DEVICE",
                        "",
                    )
                    or ""
                )
            )
        ),
        "source_mode": mode,
        "hdhomerun_enabled": (
            _source_enabled("hdhomerun")
        ),
        "native_dvb_enabled": (
            _source_enabled("native_dvb")
        ),
        "updated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "summary": {
            "total": len(tuners),
            "busy": busy,
            "idle": idle,
            "hardware_active": busy,
            "signaldvr_active": sum(
                1
                for item in tuners
                if item.get(
                    "internal_busy"
                )
            ),
        },
        "tuners": tuners,
    }



def force_release_hdhomerun(tuner_id):
    """Release an orphaned physical HDHomeRun tuner.

    Safety rules:
      * only HDHomeRun physical tuners
      * refuse SignalDVR-owned logical jobs
      * refuse tuners with an active HDHomeRun target
      * intended specifically for stale/rogue hardware locks
    """
    tuner_id = int(tuner_id)

    total = _source_max("hdhomerun")

    if tuner_id < 0 or tuner_id >= total:
        raise ValueError(
            f"Invalid HDHomeRun tuner: {tuner_id}"
        )

    hardware = _hardware_tuner_status(
        tuner_id
    )

    # Refuse a forced release if either the logical tuner allocator OR
    # SignalDVR's active live session accounts for this physical tuner.
    internal = None

    for item in status():
        if (
            int(item.get("tuner_id", -1))
            == tuner_id
            and str(
                item.get("source") or ""
            ).lower()
            == "hdhomerun"
            and item.get("state") == "busy"
        ):
            internal = item
            break

    active_hdhr = _active_hdhomerun_live()

    live_owned = False

    if active_hdhr:
        live_channel = str(
            active_hdhr.get("channel")
            or ""
        ).strip()

        hardware_channel = str(
            hardware.get("virtual_channel")
            or ""
        ).strip()

        live_owned = (
            bool(live_channel)
            and live_channel == hardware_channel
        )

    if internal or live_owned:
        raise RuntimeError(
            "Refusing to release tuner because "
            "SignalDVR owns this tuner"
        )

    if not hardware.get("active"):
        return {
            "ok": True,
            "released": False,
            "already_idle": True,
            "tuner_id": tuner_id,
        }

    target = str(
        hardware.get("target")
        or ""
    ).strip()

    if target:
        raise RuntimeError(
            "Refusing to release tuner because "
            f"it has an active target: {target}"
        )

    device = str(
        getattr(
            config,
            "HDHR_DEVICE",
            "",
        )
        or ""
    ).strip()

    if not device:
        raise RuntimeError(
            "HDHomeRun device is not configured"
        )

    commands = [
        [
            "hdhomerun_config",
            device,
            "set",
            f"/tuner{tuner_id}/target",
            "none",
        ],
        [
            "hdhomerun_config",
            device,
            "set",
            f"/tuner{tuner_id}/channel",
            "none",
        ],
    ]

    errors = []

    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )

        if result.returncode != 0:
            errors.append(
                (result.stderr or result.stdout or "")
                .strip()
            )

    if errors:
        raise RuntimeError(
            "; ".join(
                error
                for error in errors
                if error
            )
            or "HDHomeRun release command failed"
        )

    # Clear realtime cache so the UI sees the release immediately.
    with _hardware_cache_lock:
        _hardware_cache["payload"] = None
        _hardware_cache[
            "updated_monotonic"
        ] = 0.0

    after = _hardware_tuner_status(
        tuner_id
    )

    print(
        "Released rogue HDHomeRun tuner:",
        tuner_id,
        "previous_channel=",
        hardware.get("virtual_channel")
        or hardware.get("channel_raw"),
        flush=True,
    )

    return {
        "ok": True,
        "released": True,
        "tuner_id": tuner_id,
        "before": hardware,
        "after": after,
    }


def realtime_status(force=False):
    """
    Return combined SignalDVR allocation + physical HDHomeRun status.

    Results are cached for less than one second. The web UI polls once per
    second only while the Tuners page is open, so normal SignalDVR operation
    performs no extra tuner-status work.
    """
    now = time.monotonic()

    with _hardware_cache_lock:
        payload = _hardware_cache.get("payload")
        age = now - float(_hardware_cache.get("updated_monotonic") or 0.0)

        if not force and payload is not None and age < _HARDWARE_CACHE_SECONDS:
            return payload

    payload = _build_realtime_payload()

    with _hardware_cache_lock:
        _hardware_cache["payload"] = payload
        _hardware_cache["updated_monotonic"] = time.monotonic()

    return payload