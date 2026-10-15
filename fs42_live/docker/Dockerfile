# Headless FieldStation42 image for streaming / server use.
# Upstream's docker/ image targets a local X display; this one needs no display.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# intel-media-va-driver-non-free (Debian non-free) is the Quick Sync driver for
# VAAPI encoding on recent Intel GPUs such as the N100; vainfo is for diagnosing it
RUN sed -i "s/^Components: main$/Components: main non-free non-free-firmware/" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata \
       intel-media-va-driver-non-free vainfo \
    && rm -rf /var/lib/apt/lists/*

# Web channels (guide, weather): pages are rendered by Chromium on a virtual display
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium xvfb fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY fs42_live/docker/requirements-headless.txt /tmp/requirements-headless.txt
RUN pip install --no-cache-dir -r /tmp/requirements-headless.txt

COPY . .

ENTRYPOINT ["/app/fs42_live/docker/entrypoint.sh"]
CMD ["python", "station_42.py", "--server"]
