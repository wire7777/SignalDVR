import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


from app import config


DVB_DIR = config.BASE / "dvb"

DEFAULT_MAP = DVB_DIR / "native_dvb_channels.conf"
DEFAULT_VDR_SCAN = DVB_DIR / "scan_frequencies.conf"


def discover_adapters():
    adapters = []

    for frontend in sorted(Path("/dev/dvb").glob("adapter*/frontend0")):
        match = re.search(r"adapter(\d+)", str(frontend))
        if not match:
            continue

        adapter = int(match.group(1))
        base = frontend.parent

        adapters.append({
            "adapter": adapter,
            "frontend": str(frontend),
            "demux": str(base / "demux0"),
            "dvr": str(base / "dvr0"),
        })

    return adapters


def scan_frequency_source(
    adapter=None,
    country="US",
    output=DEFAULT_VDR_SCAN,
):
    """
    Perform a fresh terrestrial ATSC scan with w_scan_cpp and write
    the intermediate VDR-format channel list used by build_map().

    This is the first-run/new-install discovery step. Users should not
    need to create dvb_channels.conf manually.
    """
    output = Path(output)
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    adapters = discover_adapters()

    if not adapters:
        raise RuntimeError(
            "No Native DVB adapters detected"
        )

    available = {
        int(row["adapter"])
        for row in adapters
    }

    status_by_adapter = {
        int(row["adapter"]): row
        for row in adapter_status()
    }

    if adapter is None:
        idle = [
            number
            for number in sorted(available)
            if not status_by_adapter.get(
                number,
                {},
            ).get("busy", False)
        ]

        if not idle:
            raise RuntimeError(
                "All Native DVB adapters are currently busy"
            )

        adapter = idle[0]
    else:
        adapter = int(adapter)

        if adapter not in available:
            raise RuntimeError(
                f"Native DVB adapter {adapter} not found"
            )

        if status_by_adapter.get(
            adapter,
            {},
        ).get("busy", False):
            raise RuntimeError(
                f"Native DVB adapter {adapter} is currently busy"
            )

    if shutil.which("w_scan_cpp") is None:
        raise RuntimeError(
            "w_scan_cpp is not installed"
        )

    cmd = [
        "w_scan_cpp",
        "-fa",
        "-c",
        str(country or "US").upper(),
        "-a",
        str(adapter),
    ]

    print(
        "Native DVB frequency scan:",
        " ".join(cmd),
        flush=True,
    )

    # w_scan_cpp writes scan progress to stderr and the final
    # VDR-format channel list to stdout. Write stdout directly to a
    # temporary file while leaving stderr attached to SignalDVR so scan
    # progress remains visible in the terminal/journal.
    temporary_output = output.with_suffix(
        output.suffix + ".tmp"
    )

    temporary_output.unlink(
        missing_ok=True
    )

    try:
        with temporary_output.open(
            "w"
        ) as output_handle:
            result = subprocess.run(
                cmd,
                stdout=output_handle,
                stderr=None,
                text=True,
                check=False,
            )

        if result.returncode != 0:
            raise RuntimeError(
                "w_scan_cpp failed "
                f"with exit code {result.returncode}"
            )

        if (
            not temporary_output.exists()
            or temporary_output.stat().st_size == 0
        ):
            raise RuntimeError(
                "w_scan_cpp completed but returned no channel data"
            )

        # Validate the result before replacing the previous good scan.
        frequencies = frequencies_from_vdr(
            temporary_output
        )

        if not frequencies:
            raise RuntimeError(
                "w_scan_cpp completed but no DVB frequencies were found"
            )

        temporary_output.replace(
            output
        )

    finally:
        temporary_output.unlink(
            missing_ok=True
        )

    if not frequencies:
        raise RuntimeError(
            "w_scan_cpp completed but no DVB frequencies were found"
        )

    print(
        "Native DVB frequency scan complete:",
        f"{len(frequencies)} frequencies",
        f"output={output}",
        flush=True,
    )

    return {
        "adapter": adapter,
        "country": str(country or "US").upper(),
        "frequency_count": len(frequencies),
        "output": str(output),
    }


def scan_native_dvb(
    adapter=None,
    country="US",
):
    """
    Full SignalDVR Native DVB scan:

        w_scan_cpp
            -> local active-frequency discovery
            -> dvbv5-scan service discovery
            -> native_dvb_channels.conf

    Returns a compact result suitable for the web API.
    """
    frequency_result = scan_frequency_source(
        adapter=adapter,
        country=country,
        output=DEFAULT_VDR_SCAN,
    )

    # scan_frequency_source() may automatically select an idle
    # adapter when adapter=None. Reuse that exact adapter for the
    # dvbv5-scan service-discovery pass.
    selected_adapter = int(
        frequency_result["adapter"]
    )

    services = build_map(
        adapter=selected_adapter,
        vdr_scan=DEFAULT_VDR_SCAN,
        output=DEFAULT_MAP,
    )

    return {
        "ok": True,
        "adapter": selected_adapter,
        "country": str(country or "US").upper(),
        "frequency_count": (
            frequency_result[
                "frequency_count"
            ]
        ),
        "channel_count": len(services),
        "map_path": str(DEFAULT_MAP),
        "frequency_path": str(
            DEFAULT_VDR_SCAN
        ),
    }


def frequencies_from_vdr(path=DEFAULT_VDR_SCAN):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(path)

    frequencies = set()

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split(":")

        if len(parts) < 2:
            continue

        try:
            # w_scan_cpp VDR output uses kHz.
            freq_khz = int(parts[1])
        except ValueError:
            continue

        if freq_khz > 0:
            frequencies.add(freq_khz * 1000)

    return sorted(frequencies)


def parse_dvbv5_file(path):
    path = Path(path)

    if not path.exists():
        return []

    services = []
    current = None

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()

        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            if current:
                services.append(current)

            current = {
                "name": line[1:-1].strip()
            }
            continue

        if current is None or "=" not in line:
            continue

        key, value = line.split("=", 1)

        current[key.strip().upper()] = value.strip()

    if current:
        services.append(current)

    return services


def write_dvbv5_file(services, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    order = [
        "VCHANNEL",
        "SERVICE_ID",
        "VIDEO_PID",
        "AUDIO_PID",
        "FREQUENCY",
        "MODULATION",
        "DELIVERY_SYSTEM",
    ]

    with path.open("w") as f:
        for service in services:
            f.write(f"[{service['name']}]\n")

            written = set()

            for key in order:
                if key in service:
                    f.write(f"\t{key} = {service[key]}\n")
                    written.add(key)

            for key, value in service.items():
                if key == "name" or key in written:
                    continue

                f.write(f"\t{key} = {value}\n")

            f.write("\n")


def build_map(
    adapter=0,
    vdr_scan=DEFAULT_VDR_SCAN,
    output=DEFAULT_MAP,
):
    frequencies = frequencies_from_vdr(vdr_scan)

    if not frequencies:
        raise RuntimeError(
            f"No frequencies found in {vdr_scan}"
        )

    print(
        f"Native DVB: scanning {len(frequencies)} "
        f"known active frequencies on adapter {adapter}",
        flush=True,
    )

    all_services = {}

    with tempfile.TemporaryDirectory(
        prefix="signaldvr-native-dvb-"
    ) as tempdir:

        tempdir = Path(tempdir)

        for index, frequency in enumerate(
            frequencies,
            start=1,
        ):
            print(
                f"[{index}/{len(frequencies)}] "
                f"{frequency / 1_000_000:.3f} MHz",
                flush=True,
            )

            input_file = (
                tempdir / f"frequency_{frequency}.conf"
            )

            output_file = (
                tempdir / f"services_{frequency}.conf"
            )

            input_file.write_text(
                "[CHANNEL]\n"
                "\tDELIVERY_SYSTEM = ATSC\n"
                f"\tFREQUENCY = {frequency}\n"
                "\tMODULATION = VSB/8\n"
            )

            cmd = [
                "dvbv5-scan",
                "-a",
                str(adapter),
                "-f",
                "0",
                "-F",
                "-o",
                str(output_file),
                str(input_file),
            ]

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # dvbv5-scan on this machine currently prints
            # INVERSION/NIT warnings even when it successfully
            # locks and creates a correct service file.
            if output_file.exists():
                services = parse_dvbv5_file(
                    output_file
                )
            else:
                services = []

            if services:
                print(
                    f"    found {len(services)} services",
                    flush=True,
                )
            else:
                print(
                    "    no services",
                    flush=True,
                )

            for service in services:
                vchannel = str(
                    service.get("VCHANNEL", "")
                ).strip()

                if not vchannel:
                    continue

                # Virtual channel is the stable SignalDVR key.
                all_services[vchannel] = service

    services = sorted(
        all_services.values(),
        key=lambda item: channel_sort_key(
            item.get("VCHANNEL", "")
        ),
    )

    write_dvbv5_file(
        services,
        output,
    )

    print()
    print(
        f"Native DVB map written: {output}"
    )
    print(
        f"Mapped channels: {len(services)}"
    )

    return services


def channel_sort_key(value):
    value = str(value or "")

    try:
        major, minor = value.split(".", 1)
        return (int(major), int(minor))
    except Exception:
        return (99999, value)


def load_map(path=DEFAULT_MAP):
    return parse_dvbv5_file(path)


def get_service(channel, path=DEFAULT_MAP):
    channel = str(channel).strip()

    for service in load_map(path):
        if str(service.get("VCHANNEL", "")).strip() == channel:
            return service

    return None


def zap_command(
    channel,
    adapter,
    path=DEFAULT_MAP,
    output="-",
):
    service = get_service(channel, path)

    if not service:
        raise KeyError(
            f"Native DVB channel not mapped: {channel}"
        )

    return [
        "dvbv5-zap",
        "-a",
        str(adapter),
        "-f",
        "0",
        "-c",
        str(path),
        service["name"],
        "-r",
        "-p",
        "-o",
        str(output),
    ]


def print_status():
    adapters = discover_adapters()

    print("Native DVB adapters:")

    if not adapters:
        print("  none")
    else:
        for adapter in adapters:
            print(
                f"  adapter{adapter['adapter']}  "
                f"{adapter['frontend']}"
            )

    print()

    services = load_map()

    print(
        f"Native DVB mapped channels: "
        f"{len(services)}"
    )


def print_channels():
    services = load_map()

    for service in services:
        print(
            f"{service.get('VCHANNEL', '?'):>6}  "
            f"{service.get('name', '?'):<20}  "
            f"{service.get('FREQUENCY', '?')} Hz  "
            f"service={service.get('SERVICE_ID', '?')}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="SignalDVR Native DVB helper"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("status")
    sub.add_parser("channels")

    build = sub.add_parser("build-map")
    build.add_argument(
        "--adapter",
        type=int,
        default=0,
    )
    build.add_argument(
        "--vdr-scan",
        default=str(DEFAULT_VDR_SCAN),
    )
    build.add_argument(
        "--output",
        default=str(DEFAULT_MAP),
    )

    lookup = sub.add_parser("lookup")
    lookup.add_argument("channel")

    args = parser.parse_args()

    if args.command == "status":
        print_status()

    elif args.command == "channels":
        print_channels()

    elif args.command == "build-map":
        build_map(
            adapter=args.adapter,
            vdr_scan=args.vdr_scan,
            output=args.output,
        )

    elif args.command == "lookup":
        service = get_service(args.channel)

        if not service:
            raise SystemExit(
                f"Channel {args.channel} not found"
            )

        for key, value in service.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Native DVB adapter allocation
# ---------------------------------------------------------------------------

NATIVE_DVB_LOCK_DIR = Path("/tmp/signaldvr-native-dvb")


def _pid_alive(pid):
    import os

    try:
        pid = int(pid)
    except Exception:
        return False

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _adapter_lock_path(adapter):
    return NATIVE_DVB_LOCK_DIR / f"adapter{int(adapter)}.lock"


def acquire_adapter(owner, preferred=None):
    """
    Reserve one physical /dev/dvb adapter.

    Locks survive between StreamSession objects but stale locks from a
    crashed SignalDVR process are automatically removed.
    """
    import json
    import os

    NATIVE_DVB_LOCK_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    available = [
        int(row["adapter"])
        for row in discover_adapters()
    ]

    if not available:
        raise RuntimeError(
            "No Native DVB adapters found"
        )

    ordered = []

    if preferred is not None:
        try:
            preferred = int(preferred)
            if preferred in available:
                ordered.append(preferred)
        except Exception:
            pass

    for adapter in available:
        if adapter not in ordered:
            ordered.append(adapter)

    owner = str(owner or "")
    process_pid = os.getpid()

    for adapter in ordered:
        lock_path = _adapter_lock_path(adapter)

        # Clean stale locks first.
        if lock_path.exists():
            try:
                data = json.loads(
                    lock_path.read_text()
                )

                if (
                    str(data.get("owner") or "")
                    == owner
                    and int(data.get("pid") or 0)
                    == process_pid
                ):
                    return adapter

                if not _pid_alive(
                    data.get("pid")
                ):
                    lock_path.unlink(
                        missing_ok=True
                    )
            except Exception:
                try:
                    lock_path.unlink(
                        missing_ok=True
                    )
                except Exception:
                    pass

        try:
            fd = os.open(
                lock_path,
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            continue

        try:
            payload = {
                "adapter": adapter,
                "owner": owner,
                "pid": process_pid,
            }

            os.write(
                fd,
                json.dumps(payload).encode(
                    "utf-8"
                ),
            )
        finally:
            os.close(fd)

        return adapter

    raise RuntimeError(
        "All Native DVB tuners are busy"
    )


def release_adapter(adapter, owner=None):
    if adapter is None:
        return

    import json

    lock_path = _adapter_lock_path(adapter)

    if not lock_path.exists():
        return

    if owner:
        try:
            data = json.loads(
                lock_path.read_text()
            )

            if (
                str(data.get("owner") or "")
                != str(owner)
            ):
                return
        except Exception:
            return

    lock_path.unlink(missing_ok=True)


def adapter_status():
    import json

    result = []

    for row in discover_adapters():
        adapter = int(row["adapter"])
        lock_path = _adapter_lock_path(
            adapter
        )

        item = dict(row)
        item["busy"] = False
        item["owner"] = ""

        if lock_path.exists():
            try:
                data = json.loads(
                    lock_path.read_text()
                )

                if _pid_alive(
                    data.get("pid")
                ):
                    item["busy"] = True
                    item["owner"] = str(
                        data.get("owner") or ""
                    )
                else:
                    lock_path.unlink(
                        missing_ok=True
                    )
            except Exception:
                pass

        result.append(item)

    return result
