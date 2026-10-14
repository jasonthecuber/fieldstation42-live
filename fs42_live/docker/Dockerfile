# Headless FieldStation42 image for streaming / server use.
# Upstream's docker/ image targets a local X display; this one needs no display.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY fs42_live/docker/requirements-headless.txt /tmp/requirements-headless.txt
RUN pip install --no-cache-dir -r /tmp/requirements-headless.txt

COPY . .

ENTRYPOINT ["/app/fs42_live/docker/entrypoint.sh"]
CMD ["python", "station_42.py", "--server"]
