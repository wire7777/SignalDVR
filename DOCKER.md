# SignalDVR with Docker

SignalDVR supports native Linux and Docker from the same repository.
Docker includes Python, FFmpeg/ffprobe, SQLite tools, Waitress, and HDHomeRun utilities.

SignalDVR defaults to 8 Waitress web threads. This is suitable for most hosts and is independent of FFmpeg tuner/recording processes. Set `SIGNALDVR_WEB_THREADS=4` for a very small device, or `12`–`16` for a larger server with many simultaneous clients.

## Requirements

Use Docker Engine from Docker's official Ubuntu repository. The Snap Docker package is not supported because its confinement can block bind mounts such as `/opt` and external recording storage.

## Install from GitHub

```bash
git clone https://github.com/wire7777/SignalDVR.git
cd SignalDVR
cp .env.example .env
mkdir -p data/{config,recordings,timeshift,thumbnails,logs,guide}
docker compose up -d --build
```

Open `http://SERVER-IP:8088/settings` and configure tuners, guide data, storage, and DVR settings.

## Production storage

Edit `.env` and replace the `./data/...` values with absolute host paths. Example:

```dotenv
SIGNALDVR_RECORDINGS_HOST=/mnt/Storage/SignalDVR_Recordings
SIGNALDVR_TIMESHIFT_HOST=/mnt/Storage/SignalDVR_TimeShift
SIGNALDVR_THUMBNAILS_HOST=/mnt/Storage/SignalDVR_Thumbnail
```

Then recreate the container:

```bash
docker compose up -d
```

## Logs and status

```bash
docker compose ps
docker compose logs -f signaldvr
docker compose exec signaldvr ffmpeg -version
```

## Database

The Docker database is persisted at `SIGNALDVR_CONFIG_HOST/signaldvr.db`. The Settings page can create, download, upload, and restore consistent SQLite backups.

## Update

```bash
git pull
docker compose up -d --build
```
