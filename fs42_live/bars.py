"""Black bars made part of the picture: a widescreen music video put out as 4:3 with the letterbox in it,
say, or a 4:3 show put out as 16:9 with bars at its sides. They're cut off before anything else, so a 4:3
picture fills the 4:3 screen instead of sitting small in the middle of it, and a widescreen one zooms
like any other (zoom crops a picture's sides down to a shape, which does nothing to one already 4:3).

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

STORE = "runtime/bars.json"  # realpath -> {"size": bytes, "crop": [w, h, x, y] or None, "v": VERSION}
SAMPLES = (0.1, 0.3, 0.5, 0.7, 0.9)  # where through the file to look
SAMPLE_SECONDS = 2
LEAST = 0.08  # bars thinner than this (of the height, or the width) are left alone: edge noise, not bars
EVEN = 0.04  # bars are as thick on one side as the other, give or take this (of the width, or the height)
NARROWEST = 0.6  # and leave at least this much of the width
VERSION = 4  # a crop found before 2 (when uneven or narrow ones counted) is looked for again, and so is
# none found before 3 (when a logo on one of the bars hid them) or before 4 (when a 10-bit video's black,
# which is 64 not 16, never counted as black)
BLACK = 0.094  # of the brightest a pixel can be, so 24 in an 8-bit video and 96 in a 10-bit one
CHECK_EVERY = 600
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".webm", ".wmv", ".flv"}
DEEP = re.compile(r"p01[026]|(9|1[0-6])(le|be)$")  # pixel formats with more than 8 bits
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
    if not found or found["size"] != size or found.get("v", 1) < (2 if found["crop"] else 4):
        return False
    return found["crop"]


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
    """[w, h, x, y] of the picture inside a file's bars, or None if it has none worth cutting. The middle
    sample by size, so neither a dark scene (smaller) nor one bright shot over the bars (bigger) decides it."""
    boxes, size = [], None
    for at in SAMPLES:
        log = subprocess.run(
            ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{duration * at:.1f}", "-i", path,
             "-t", str(SAMPLE_SECONDS), "-an", "-sn", "-dn", "-vf", f"cropdetect=limit={BLACK}:round=2:reset=0",
             "-f", "null", "-"],
            capture_output=True, text=True, errors="replace").stderr
        size = size or re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})(?: \[SAR (\d+):(\d+))?", log)
        found = CROP.findall(log)
        if found:
            w, h, x, y = map(int, found[-1])
            if w > 0 and h > 0:
                boxes.append((w, h, x, y))
    if not boxes or not size:
        return None
    width, height = int(size[1]), int(size[2])
    w, h, x, y = sorted(boxes, key=lambda b: b[0] * b[1])[len(boxes) // 2]
    if h > height * (1 - LEAST) and w > width * (1 - LEAST):
        return None
    # a logo on one of a 4:3 picture's side bars (Sesame Street's uploads) stretches the picture found
    # over to it: the other side's bar, as thick on both, leaves the 4:3 picture
    side, sar = max(x, width - w - x), int(size[3] or 1) / int(size[4] or 1)
    if h > height * (1 - LEAST) and abs((width - 2 * side) * sar / h - 4 / 3) < 0.03:
        w, x = width - 2 * side, side
    # a title card's black, or a dark scene's, or a picture boxed into one corner, isn't bars
    if abs(x - (width - w - x)) > width * EVEN or abs(y - (height - h - y)) > height * EVEN or w < width * NARROWEST:
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
            was = _load().get(path)
        if _black_was_fine(path, was):
            with _lock:
                was["v"] = VERSION
                done += 1
                if done % 50 == 0:
                    _save()
            continue
        crop = find(path, duration or 0)
        with _lock:
            had = path in _load()
            was = (_load().get(path) or {}).get("crop")
            _load()[path] = {"size": os.path.getsize(path), "crop": crop, "v": VERSION}
            done += 1
            if done % 50 == 0:
                _save()
        if had and was != crop:
            _forget_inside(path)
        if crop:
            _l.info(f"bars: {os.path.basename(path)}: picture {crop[0]}x{crop[1]}")
    if done:
        with _lock:
            _save()
        _l.info(f"bars: looked at {done} files")


def _forget_inside(path):
    """The logos and pan and scan found inside a file's old, wrong bars are looked for again."""
    from fs42_live import logos, panscan  # they import this module
    for found in (logos, panscan):
        with found._lock:
            if found._load().pop(path, None) is not None:
                found._save()


def _black_was_fine(path, was):
    """No bars found before 4 in a file that isn't 10-bit (or more) was right: its black always counted."""
    if not was or was["crop"] or was.get("v", 1) != 3 or was["size"] != os.path.getsize(path):
        return False
    fmt = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt",
                          "-of", "csv=p=0", path], capture_output=True, text=True, errors="replace").stdout.strip()
    return bool(fmt) and not DEEP.search(fmt)


def _probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, errors="replace").stdout
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
