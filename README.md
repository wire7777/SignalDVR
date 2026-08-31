# SignalDVR

**SignalDVR** is a self-hosted over-the-air DVR for Linux, Docker, Android TV, web browsers, and Kodi. It combines HDHomeRun tuners, FFmpeg, SQLite, HLS, Schedules Direct/XMLTV guide data, and Media3 playback into a lightweight home DVR platform.

> **Project status:** Active beta development. Core live TV, timeshift, recording, playback, guide, library, and Background DVR features are working. Back up your database before upgrading and test new releases before using them on a production DVR.

## Highlights

- Live OTA television through HDHomeRun tuners
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
- Kodi client package included
- SQLite database status, integrity check, backup, download, upload, and restore tools
- Native Linux and Docker deployment from the same repository

## Architecture

```text
HDHomeRun / OTA tuner
        |
        v
SignalDVR backend (Python + Flask + Waitress)
        |
        +-- FFmpeg HLS live/timeshift sessions
        +-- Recording scheduler and Background DVR
        +-- SQLite database and guide cache
        +-- Artwork, metadata, resume, and library APIs
        |
        +-- Web UI
        +-- Android TV Media3 client
        +-- Kodi client
```

## Requirements

### Common

- An HDHomeRun-compatible network tuner
- OTA antenna and available channels
- Linux host or Docker host
- Network access from playback clients to the SignalDVR server
- Optional Schedules Direct account or XMLTV guide source

### Native Linux

- Python 3.10 or newer; Python 3.12 is recommended
- FFmpeg and ffprobe
- SQLite
- HDHomeRun command-line utilities

### Docker

Use **Docker Engine from Docker's official repository** with the Compose plugin. The Snap Docker package is not supported because its confinement can block bind mounts and external recording storage.

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

Open:

```text
http://SERVER-IP:8088/settings
```

Follow the logs:

```bash
docker compose logs -f signaldvr
```

On a brand-new installation, open:

```text
http://SERVER-IP:8088/setup
```

SignalDVR redirects unconfigured browser requests to the first-run setup page. Background services remain paused until setup is completed.

Check container health:

```bash
docker compose ps
```

### Production storage paths

The default `.env` keeps all persistent data inside `./data`. For a production DVR, edit `.env` and use absolute host paths:

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

sudo chown -R "$USER":"$USER" /srv/signaldvr /mnt/Storage/SignalDVR_*
```

Recreate the container after changing `.env`:

```bash
docker compose up -d
```

Docker automatically includes Python, FFmpeg, ffprobe, SQLite tools, Waitress, Requests, SQLAlchemy, and HDHomeRun utilities. You do not need to install FFmpeg on the host.

The Docker image uses 8 Waitress web threads by default. This is a safe general-purpose setting and does not control FFmpeg process count. Override it in `.env` with `SIGNALDVR_WEB_THREADS=4` for very small systems or `12`–`16` for larger installations with many clients.

See [DOCKER.md](DOCKER.md) for additional Docker notes.

## Native Linux installation

Install system packages on Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg sqlite3 hdhomerun-config
```

Clone and install Python dependencies:

```bash
git clone --branch beta/playback-engine-v2 \
  https://github.com/wire7777/SignalDVR.git
cd SignalDVR

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Start SignalDVR:

```bash
python -m app.app
```

Open:

```text
http://SERVER-IP:8088/settings
```

Native installations retain the project's existing default paths unless environment variables override them.

## Initial configuration

Use the **Settings** page to configure:

1. Server and guide settings
2. HDHomeRun tuner discovery or device address
3. Schedules Direct or XMLTV guide source
4. Recording and timeshift storage
5. Tuner and simultaneous-recording limits
6. Recording padding and priorities
7. Background DVR retention
8. Artwork and guide behavior

A dedicated first-run wizard and formal schema-migration system are planned. Current beta installations use the Settings page for initial setup.

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

On the host, this corresponds to `SIGNALDVR_CONFIG_HOST/signaldvr.db`.

Always create or download a backup before updating the application.

## Updating

### Docker

```bash
git pull
docker compose up -d --build
docker compose logs -f signaldvr
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
- Guide and grid/timeline guide
- Library and recordings
- Scheduled recordings
- Series rules
- Search
- Tuners
- Health
- History
- Settings

## Android TV client

The Android TV client uses AndroidX Media3/ExoPlayer and supports:

- Live and delayed-live playback
- Recording playback
- Resume positions
- Guide overlays and channel navigation
- Quick Guide, Mini Guide, and Full Info overlays
- Closed-caption controls
- DVR seeking and jump-to-live behavior

The Android project is developed separately from the Python backend. Configure the server address and port in the Android app's Settings page.

## Kodi client

A Kodi plugin package is included in the repository:

```text
plugin.video.signaldvr.zip
```

Install it through Kodi's **Install from zip file** option, then configure the SignalDVR server address.

## Storage model

SignalDVR uses HLS media folders for live sessions, recordings, VOD preparation, and Background DVR content.

Background DVR treats each guide program as a catalog item. Temporary programs can expire according to retention settings, while saved programs are protected without copying or transcoding the media folder.

Important storage areas include:

```text
recordings/   Permanent recordings and generated VOD data
timeshift/    Live sessions and Background DVR program media
thumbnails/   Logos, posters, and artwork
guide/        Guide cache and downloaded guide data
config/       SQLite database and backups
logs/         Application logs
```

## Troubleshooting

### View Docker logs

```bash
docker compose logs --tail=200 signaldvr
docker compose logs -f signaldvr
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

## Development rules

SignalDVR's playback engine, delayed-live behavior, Media3 compatibility, guide navigation, and Background DVR lifecycle are actively used and should be changed carefully. Prefer focused changes over wholesale file replacements.

Before committing backend changes:

```bash
python3 -m py_compile app/app.py app/config.py app/database.py
```

Before committing Docker changes:

```bash
docker compose config
docker compose build
```

## Roadmap

Current priorities include:

- Complete first-run setup wizard
- Formal database schema versioning and migrations
- Production Docker image publishing through GitHub Container Registry
- Unraid/TrueNAS-friendly deployment templates
- Improved series-rule and conflict management
- Continued Android TV polish
- Optional commercial detection and skipping

## License

See [LICENSE](LICENSE).

## Project repository

```text
https://github.com/wire7777/SignalDVR
```
