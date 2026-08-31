# SignalDVR

**SignalDVR** is a self-hosted over-the-air DVR for Linux, Docker, Android TV, and web browsers. It combines HDHomeRun network tuners and native Linux DVB/PCIe tuners with FFmpeg, SQLite, HLS, Schedules Direct/XMLTV guide data, and Media3 playback into a lightweight home DVR platform.

> **Project status:** Active beta development. Core live TV, timeshift, recording, playback, guide, library, Background DVR, HDHomeRun, and native DVB tuner features are working. Back up your database before upgrading and test new releases before using them on a production DVR.

## Highlights

- Live OTA television through HDHomeRun network tuners
- Native Linux DVB/PCIe tuner support through the Linux DVB API
- Automatic tuner allocation across multiple native DVB adapters
- HDHomeRun and native DVB tuners can operate together
- Pause, rewind, fast-forward, and jump back to live
- Delayed-live and watch-from-beginning playback
- One-time and series recording rules
- Scheduled recordings, priorities, padding, and conflict handling
- Background DVR with program-based temporary recordings
- Save buffered programs without copying or transcoding media
- Media3-compatible HLS playback for Android TV
- Web guide, timeline/grid guide, library, search, recordings, tuner status, and health pages
- Schedules Direct and XMLTV guide support
- Rich program metadata, channel logos, posters, and artwork caching
- Resume playback and watched progress
- SQLite database status, integrity check, backup, download, upload, and restore tools
- Native Linux and Docker deployment from the same repository

## Architecture

```text
HDHomeRun network tuner        Native Linux DVB/PCIe tuner
          |                              |
          |                          /dev/dvb
          |                              |
          +---------------+--------------+
                          |
                          v
           SignalDVR backend
        (Python + Flask + Waitress)
                          |
                          +-- Tuner discovery and allocation
                          +-- Native DVB tuning via dvbv5-zap
                          +-- FFmpeg HLS live/timeshift sessions
                          +-- Recording scheduler and Background DVR
                          +-- SQLite database and guide cache
                          +-- Artwork, metadata, resume, and library APIs
                          |
                          +-- Web UI
                          +-- Android TV Media3 client
```

## Tuner support

SignalDVR supports two OTA tuner sources.

### HDHomeRun

HDHomeRun network tuners are discovered and accessed over the local network.

SignalDVR can use one or more available HDHomeRun tuner slots for live television and recordings.

### Native Linux DVB

SignalDVR can directly use Linux-supported DVB PCIe and USB tuners exposed through `/dev/dvb`.

No intermediate TV server is required.

A multi-tuner device may expose several Linux DVB adapters:

```text
/dev/dvb/adapter0
/dev/dvb/adapter1
/dev/dvb/adapter2
/dev/dvb/adapter3
```

SignalDVR tracks adapter ownership and automatically allocates available adapters to live TV and recording sessions.

### Combined tuner operation

HDHomeRun and native DVB can be enabled simultaneously.

SignalDVR can select between available tuner sources according to tuner availability, allowing network and PCIe tuners to contribute to the same DVR tuner pool.

Tuner sources can be enabled or disabled independently from the SignalDVR Settings page.

## Requirements

### Common

- One or more supported OTA tuners:
  - HDHomeRun-compatible network tuner, or
  - Linux DVB-compatible PCIe/USB tuner exposed through `/dev/dvb`
- OTA antenna and available channels
- Linux host or Docker host
- Network access from playback clients to the SignalDVR server
- Optional Schedules Direct account or XMLTV guide source

### Native Linux

- Python 3.10 or newer; Python 3.12 is recommended
- FFmpeg and ffprobe
- SQLite
- HDHomeRun command-line utilities when using HDHomeRun
- `dvb-tools` when using native DVB
- `w-scan-cpp` for native DVB channel scanning
- Linux-supported DVB hardware exposed under `/dev/dvb`

### Docker

Use **Docker Engine from Docker's official repository** with the Compose plugin.

The Snap Docker package is not supported because its confinement can block bind mounts and external recording storage.

Native DVB tuner passthrough requires a Linux Docker host with the tuner hardware available under `/dev/dvb`.

## Docker installation

Clone the branch you want to test. Until Docker testing is merged into `main`, use the beta branch:

```bash
git clone --branch beta/playback-engine-v2 \
  https://github.com/wire7777/SignalDVR.git
cd SignalDVR
```

Prepare the environment and persistent folders:

```bash
cp .env.example .env
mkdir -p data/{config,recordings,timeshift,thumbnails,logs,guide}
```

Build and start SignalDVR:

```bash
docker compose up -d --build
```

On a brand-new installation, open:

```text
http://SERVER-IP:8088/setup
```

SignalDVR redirects unconfigured browser requests to the first-run setup page. Background services remain paused until initial setup is completed.

After setup, the Settings page is available at:

```text
http://SERVER-IP:8088/settings
```

Follow the logs:

```bash
docker compose logs -f signaldvr
```

Check container health:

```bash
docker compose ps
```

## Native DVB tuners with Docker

The normal Docker configuration does not require `/dev/dvb`. This allows SignalDVR to run normally on systems that only use HDHomeRun network tuners.

Native DVB hardware requires access to the Linux host's DVB devices.

Use the optional DVB Compose override:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  up -d --build
```

The DVB override exposes the host's:

```text
/dev/dvb
```

devices to the SignalDVR container.

Verify the DVB adapters from inside the container:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  exec signaldvr ls -R /dev/dvb
```

A four-tuner PCIe card, for example, may expose:

```text
/dev/dvb/adapter0
/dev/dvb/adapter1
/dev/dvb/adapter2
/dev/dvb/adapter3
```

SignalDVR can then allocate those adapters independently for live TV and recording sessions.

PCIe DVB passthrough is intended for Linux Docker hosts. Docker Desktop on Windows can be used with HDHomeRun network tuners but is not a practical target for direct PCIe DVB tuner access.

## Production storage paths

The default `.env` keeps persistent data inside `./data`.

For a production DVR, edit `.env` and use absolute host paths:

```dotenv
TZ=America/Los_Angeles
SIGNALDVR_PORT=8088

SIGNALDVR_CONFIG_HOST=/srv/signaldvr/config
SIGNALDVR_RECORDINGS_HOST=/mnt/Storage/SignalDVR_Recordings
SIGNALDVR_TIMESHIFT_HOST=/mnt/Storage/SignalDVR_TimeShift
SIGNALDVR_THUMBNAILS_HOST=/mnt/Storage/SignalDVR_Thumbnail
SIGNALDVR_LOGS_HOST=/srv/signaldvr/logs
SIGNALDVR_GUIDE_HOST=/srv/signaldvr/guide
```

Create the host folders before starting the container:

```bash
sudo mkdir -p \
  /srv/signaldvr/config \
  /srv/signaldvr/logs \
  /srv/signaldvr/guide \
  /mnt/Storage/SignalDVR_Recordings \
  /mnt/Storage/SignalDVR_TimeShift \
  /mnt/Storage/SignalDVR_Thumbnail

sudo chown -R "$USER":"$USER" \
  /srv/signaldvr \
  /mnt/Storage/SignalDVR_*
```

Recreate the container after changing `.env`:

```bash
docker compose up -d
```

Docker automatically includes the application dependencies required by SignalDVR, including Python, FFmpeg, ffprobe, SQLite tools, Waitress, Requests, SQLAlchemy, and tuner utilities.

You do not need to install FFmpeg on the Docker host.

The Docker image uses 8 Waitress web threads by default. This is a safe general-purpose setting and does not control FFmpeg process count.

Override it in `.env` with:

```dotenv
SIGNALDVR_WEB_THREADS=4
```

for very small systems, or approximately:

```dotenv
SIGNALDVR_WEB_THREADS=12
```

to:

```dotenv
SIGNALDVR_WEB_THREADS=16
```

for larger installations with many clients.

See [DOCKER.md](DOCKER.md) for additional Docker notes.

## Native Linux installation

Install the common system packages on Ubuntu/Debian:

```bash
sudo apt update

sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  ffmpeg \
  sqlite3 \
  hdhomerun-config \
  dvb-tools \
  w-scan-cpp
```

If you are only using HDHomeRun, the DVB packages are not required.

If you are only using native DVB hardware, the HDHomeRun utilities are not required.

Clone SignalDVR:

```bash
git clone --branch beta/playback-engine-v2 \
  https://github.com/wire7777/SignalDVR.git

cd SignalDVR
```

Create the Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install Python dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Start SignalDVR:

```bash
python -m app.app
```

On a new installation, open:

```text
http://SERVER-IP:8088/setup
```

After configuration, use:

```text
http://SERVER-IP:8088/settings
```

Native installations retain the project's existing default paths unless environment variables override them.

## Native DVB / PCIe tuner setup

SignalDVR can use Linux DVB tuners directly without an intermediate TV server.

The tuner must first be recognized by the Linux kernel.

Check for DVB devices:

```bash
ls -R /dev/dvb
```

A multi-tuner card may appear as:

```text
/dev/dvb/adapter0:
demux0
dvr0
frontend0
net0

/dev/dvb/adapter1:
demux0
dvr0
frontend0
net0
```

Additional tuners appear as additional adapters.

Verify that the required DVB utilities are installed:

```bash
which dvbv5-zap
which dvbv5-scan
which w_scan_cpp
```

SignalDVR uses `dvbv5-zap` for native tuner operation and `w_scan_cpp`/DVB scanning tools for channel discovery.

Use the SignalDVR **Settings** page to:

1. Enable Native DVB
2. Detect available adapters
3. Scan for OTA channels
4. Review the resulting channel map
5. Enable or disable tuner sources

The native DVB channel configuration is stored by SignalDVR and used to map virtual channel numbers to DVB services.

Once configured, SignalDVR can allocate free DVB adapters automatically.

## Initial configuration

The first-run setup page is available at:

```text
http://SERVER-IP:8088/setup
```

Use initial setup and the **Settings** page to configure:

1. Server settings
2. Tuner sources
   - HDHomeRun discovery/device address
   - Native DVB adapters and channel scan
   - HDHomeRun + Native DVB combined operation
3. Schedules Direct or XMLTV guide source
4. Recording and timeshift storage
5. Tuner and simultaneous-recording limits
6. Recording padding and priorities
7. Background DVR retention
8. Artwork and guide behavior

Unconfigured installations redirect browser requests to the setup page, and background services remain paused until setup is completed.

Additional configuration and advanced options remain available through the Settings page after initial setup.

## Database backup and restore

The Settings page includes a Database section with:

- Database path and total size
- Integrity status
- Schema/table information
- Last-modified and last-backup details
- Create Backup
- Download Current Database
- Upload and Restore Database
- Automatic pre-restore safety backup

Docker stores the active database at:

```text
/config/signaldvr.db
```

On the host, this corresponds to:

```text
SIGNALDVR_CONFIG_HOST/signaldvr.db
```

Always create or download a database backup before updating SignalDVR.

For important installations, keep at least one backup outside the SignalDVR server.

## Updating

Back up the SignalDVR database before updating.

### Docker

```bash
git pull
docker compose up -d --build
docker compose logs -f signaldvr
```

If using native DVB with the Compose override:

```bash
git pull

docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  up -d --build
```

### Native Linux

```bash
git pull

source venv/bin/activate

pip install -r requirements.txt
```

Then restart the SignalDVR process or service.

## Web interface

The web application includes pages for:

- Home/dashboard
- Live TV
- Guide
- Grid/timeline guide
- Library
- Recordings
- Scheduled recordings
- Series rules
- Search
- Tuners
- Health
- History
- Settings

The Tuners page provides visibility into tuner availability and active tuner usage.

## Android TV client

The Android TV client uses AndroidX Media3/ExoPlayer.

Current functionality includes:

- Live TV playback
- Delayed-live playback
- Pause and resume live TV
- Rewind and fast-forward
- Jump to live
- Watch from beginning
- Recording playback
- Resume positions
- Watched progress
- Guide overlays
- Channel navigation
- Quick Guide
- Mini Guide
- Full Info overlays
- Closed-caption controls
- DVR seeking
- Live playback recovery behavior

The Android application communicates with the SignalDVR backend over the local network.

Configure the SignalDVR server address and port in the Android application's Settings page.

The Android application is developed independently from the Python server code and may have its own beta development branch.

## Storage model

SignalDVR uses HLS media folders for live sessions, recordings, VOD preparation, and Background DVR content.

Background DVR treats each guide program as a catalog item.

Temporary programs can expire according to the configured retention policy, while saved programs are protected without copying or transcoding the media folder.

Important storage areas include:

```text
recordings/   Permanent recordings and generated VOD data
timeshift/    Live sessions and Background DVR program media
thumbnails/   Logos, posters, and artwork
guide/        Guide cache and downloaded guide data
config/       SQLite database and backups
logs/         Application logs
```

Timeshift storage can grow substantially during normal DVR operation. Production installations should place timeshift and recording storage on disks sized appropriately for the number of tuners, retention settings, and expected recording volume.

## Background DVR

Background DVR continuously organizes buffered live television around guide programs.

Instead of treating the live buffer as one anonymous stream, SignalDVR tracks media belonging to individual guide programs.

This enables features such as:

- Watch from beginning
- Delayed-live playback
- Program-based temporary buffering
- Saving a buffered program
- Retention-based cleanup
- Reusing existing media without unnecessary copying or transcoding

Saved programs are protected from normal temporary-buffer expiration.

## Troubleshooting

### View Docker logs

```bash
docker compose logs --tail=200 signaldvr
docker compose logs -f signaldvr
```

For native DVB Docker installations:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  logs --tail=200 signaldvr
```

### Verify FFmpeg inside Docker

```bash
docker compose exec signaldvr ffmpeg -version
docker compose exec signaldvr ffprobe -version
```

### Verify Python dependencies

```bash
docker compose exec signaldvr python -c \
  "import flask, requests, sqlalchemy, waitress; print('dependencies OK')"
```

### Check the health endpoint

```bash
curl -fsS http://127.0.0.1:8088/health
```

### Check persistent folder permissions

```bash
ls -ld data data/*
```

The container must be able to write to every mounted configuration, recording, timeshift, thumbnail, log, and guide folder.

### Check HDHomeRun discovery

If using an HDHomeRun tuner:

```bash
hdhomerun_config discover
```

Verify that the SignalDVR server can reach the HDHomeRun over the network.

### Check native DVB adapters

If using a PCIe or USB DVB tuner:

```bash
ls -R /dev/dvb
```

Check the available frontends:

```bash
find /dev/dvb -name 'frontend*' -print
```

Verify DVB utilities:

```bash
dvbv5-zap --help
dvbv5-scan --help
w_scan_cpp --help
```

If `/dev/dvb` does not exist, verify that Linux recognizes the tuner and that the appropriate kernel driver is loaded before troubleshooting SignalDVR itself.

### Check native DVB inside Docker

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  exec signaldvr ls -R /dev/dvb
```

If the devices exist on the host but not inside the container, verify that the DVB Compose override was included when the container was started.

### Playback problems

When troubleshooting playback, determine whether the issue originates from:

1. The tuner/input transport stream
2. FFmpeg
3. SignalDVR's HLS/timeshift session
4. Network delivery
5. The playback client

SignalDVR intentionally keeps these layers separate so playback problems can be isolated without unnecessarily changing unrelated components.

## Development rules

SignalDVR's playback engine, delayed-live behavior, Media3 compatibility, guide navigation, tuner allocation, native DVB handling, and Background DVR lifecycle are actively used and should be changed carefully.

Prefer focused changes over wholesale file replacements.

Before committing backend changes:

```bash
python3 -m py_compile \
  app/app.py \
  app/config.py \
  app/database.py
```

When changing native DVB support, also compile the affected tuner modules:

```bash
python3 -m py_compile \
  app/native_dvb.py \
  app/tuner_manager.py
```

Check the complete Python package when appropriate:

```bash
python3 -m compileall -q app
```

Check Git whitespace/errors:

```bash
git diff --check
```

Before committing Docker changes:

```bash
docker compose config
docker compose build
```

For the native DVB Docker configuration:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dvb.yml \
  config
```

## Roadmap

Current priorities include:

- Continued native DVB/PCIe tuner testing and hardware compatibility improvements
- Continued first-run setup improvements
- Formal database schema versioning and migrations
- Production Docker image publishing through GitHub Container Registry
- Unraid/TrueNAS-friendly deployment templates
- Improved series-rule and recording-conflict management
- Continued Android TV polish
- Playback reliability and recovery improvements
- Additional tuner diagnostics and health reporting
- Optional commercial detection and skipping

## Beta software notice

SignalDVR is under active development.

Before upgrading:

1. Back up the SQLite database.
2. Preserve important recordings.
3. Review changes before deploying them to a production DVR.
4. Test major playback, tuner, or storage changes before relying on them for scheduled recordings.

Bug reports should include relevant SignalDVR logs and enough information to identify the tuner source, channel, playback client, and deployment type.

## License

See [LICENSE](LICENSE).

## Project repository

https://github.com/wire7777/SignalDVR
