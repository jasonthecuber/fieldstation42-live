"""Black bars made part of the picture: a widescreen music video put out as 4:3 with the letterbox in it,
say. Zoom crops a picture's sides down to a shape, which does nothing to one already 4:3, so its bars are
cut off first and the picture inside zooms like any widescreen one.

Each video file's bars are found once, in the background, from a few short samples through it. A file
not looked at yet plays as it is, and is queued.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading

from fs42.catalog_api import CatalogAPI
from fs42.station_manager import StationManager

_l = logging.getLogger("FS42LIVE")

STORE = "runtime/bars.json"  # realpath -> {"size": bytes, "crop": [w, h, x, y] or None}
SAMPLES = (0.15, 0.4, 0.65, 0.85)  # where through the file to look
SAMPLE_SECONDS = 2
LEAST = 0.08  # bars thinner than this (of the height, or the width) are left alone: edge noise, not bars
CHECK_EVERY = 600
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".webm", ".wmv", ".flv"}
CROP = re.compile(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)")

_lock = threading.Lock()
_known = None
_queued = set()


def _load():
    global _known
    if _known is None:
        try:
            with open(STORE) as f:
                _known = json.load(f)
        except FileNotFoundError:
            _known = {}
    return _known


def _save():
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_known, f, indent=0)
    os.replace(tmp, STORE)


def _found(path):
    """The stored bars if they're for this version of the file, else False."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    found = _load().get(path)
    return found["crop"] if found and found["size"] == size else False


def crop_filter(path):
    """The crop that cuts a file's bars off, for the start of the encoder's -vf, or ""."""
    if path is None or os.path.splitext(path)[1].lower() not in VIDEO_EXTS:
        return ""
    path = os.path.realpath(path)
    with _lock:
        crop = _found(path)
        if crop is False:
            _queued.add(path)
    return f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""


def find(path, duration):
    """[w, h, x, y] of the picture inside a file's bars, or None if it has none worth cutting. The samples'
    pictures together, so a dark scene in one doesn't shrink it."""
    boxes, size = [], None
    for at in SAMPLES:
        log = subprocess.run(
            ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{duration * at:.1f}", "-i", path,
             "-t", str(SAMPLE_SECONDS), "-an", "-sn", "-dn", "-vf", "cropdetect=limit=24:round=2:reset=0",
             "-f", "null", "-"],
            capture_output=True, text=True).stderr
        size = size or re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", log)
        found = CROP.findall(log)
        if found:
            w, h, x, y = map(int, found[-1])
            if w > 0 and h > 0:
                boxes.append((x, y, x + w, y + h))
    if not boxes or not size:
        return None
    width, height = int(size[1]), int(size[2])
    x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    w, h = max(b[2] for b in boxes) - x, max(b[3] for b in boxes) - y
    if h > height * (1 - LEAST) and w > width * (1 - LEAST):
        return None
    return [w, h, x, y]


def scan_new_files():
    """Looks at every catalog video (shortest first) and anything queued by the encoder, skipping
    files already looked at."""
    paths = {}
    for station in StationManager().stations:
        if station.get("_has_catalog"):
            for entry in CatalogAPI.get_entries(station):
                path = entry.realpath or os.path.realpath(entry.path)
                if entry.duration and os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                    paths[path] = entry.duration
    with _lock:
        queued = set(_queued)
        _queued.clear()
    for path in queued - set(paths):
        paths[path] = _probe_duration(path)
    done = 0
    for path, duration in sorted(paths.items(), key=lambda p: p[1] or 0):
        with _lock:
            if _found(path) is not False or not os.path.exists(path):
                continue
        crop = find(path, duration or 0)
        with _lock:
            _load()[path] = {"size": os.path.getsize(path), "crop": crop}
            done += 1
            if done % 50 == 0:
                _save()
        if crop:
            _l.info(f"bars: {os.path.basename(path)}: picture {crop[0]}x{crop[1]}")
    if done:
        with _lock:
            _save()
        _l.info(f"bars: looked at {done} files")


def _probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0


async def keep_found():
    while True:
        try:
            await asyncio.to_thread(scan_new_files)
        except Exception:
            _l.exception("bars: scan failed")
        await asyncio.sleep(CHECK_EVERY)
