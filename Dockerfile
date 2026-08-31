FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SIGNALDVR_CONTAINER=1 \
    SIGNALDVR_BASE=/config \
    SIGNALDVR_DATABASE=/config/signaldvr.db \
    SIGNALDVR_RECORDINGS=/recordings \
    SIGNALDVR_TIMESHIFT=/timeshift \
    SIGNALDVR_THUMBNAILS=/thumbnails \
    SIGNALDVR_LOGS=/logs \
    SIGNALDVR_GUIDE=/guide \
    SIGNALDVR_PORT=8088

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      curl \
      ca-certificates \
      tzdata \
      sqlite3 \
      hdhomerun-config \
      dvb-tools \
      w-scan-cpp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/signaldvr
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && python -c "import flask, requests, sqlalchemy, waitress, werkzeug; print('Python dependencies OK')"
COPY . .
RUN chmod +x docker/entrypoint.sh \
    && mkdir -p /config /recordings /timeshift /thumbnails /logs /guide

EXPOSE 8088
VOLUME ["/config", "/recordings", "/timeshift", "/thumbnails", "/logs", "/guide"]
ENTRYPOINT ["/opt/signaldvr/docker/entrypoint.sh"]
