"""Evens out loudness between files, so a quiet movie and a loud ad play at about the same level.

Each file's loudness (EBU R128 integrated, in LUFS) is measured once, in the background, and the
encoder turns it up or down to `loudness_target` (fs42_live/settings.py). Turning up can push
peaks past full scale, so a limiter follows. A file not measured yet plays as it is, and is
queued, so a bump or slate that isn't in any catalog gets measured the first time it plays.
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
from fs42_live import settings

_l = logging.getLogger("FS42LIVE")

STORE = "runtime/loudness.json"  # realpath -> {"size": bytes, "lufs": measured or None}
MOST_UP = 10.0  # dB; more would mostly raise hiss
MOST_DOWN = -15.0
SILENT = -50.0  # LUFS; quieter than this is a silent clip, left alone
CHECK_EVERY = 600  # seconds; often, since files the encoder meets get queued
AUDIO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".webm", ".wmv", ".flv"}

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


def _measured(path):
    """The stored loudness if it's for this version of the file, else False."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    found = _load().get(path)
    return found["lufs"] if found and found["size"] == size else False


def audio_filter(path):
    """The encoder's -af chain for a file: in sync, then levelled."""
    chain = "aresample=async=1"
    if path is None or os.path.splitext(path)[1].lower() not in AUDIO_EXTS:
        return chain
    path = os.path.realpath(path)
    with _lock:
        lufs = _measured(path)
        if lufs is False:
            _queued.add(path)
            return chain
    if lufs is None or lufs < SILENT:
        return chain
    return chain + _levelled(lufs)


def music_filter(tracks):
    """The -af chain for a web page channel's background music. Its playlist is one input, so it gets one
    gain, from its tracks' middle loudness (music is mastered alike, near enough); a track not measured yet
    is queued. (Levelling it as it plays, with loudnorm, held up the screen capture: the page went choppy.)"""
    chain, levels = "aresample=async=1", []
    with _lock:
        for track in tracks:
            path = os.path.realpath(track)
            lufs = _measured(path)
            if lufs is False:
                _queued.add(path)
            elif lufs is not None and lufs >= SILENT:
                levels.append(lufs)
    return chain + _levelled(sorted(levels)[len(levels) // 2]) if levels else chain


def _levelled(lufs):
    gain = max(MOST_DOWN, min(MOST_UP, settings.get("loudness_target") - lufs))
    if abs(gain) < 0.5:
        return ""
    chain = f",volume={gain:.1f}dB"
    if gain > 0:
        chain += ",alimiter=limit=0.89:level=false"  # peaks held under -1 dBFS
    return chain


def measure(path):
    """Integrated loudness in LUFS, or None if the file has no audio."""
    log = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-vn", "-sn", "-dn", "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    summary = log.rpartition("Summary:")[2]
    found = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    return float(found.group(1)) if found else None


def scan_new_files():
    """Measures every catalog file (shortest first, so the ads are done early) and anything
    queued by the encoder, skipping files already measured."""
    paths = set()
    for station in StationManager().stations:
        if station.get("_has_catalog"):
            for entry in sorted(CatalogAPI.get_entries(station), key=lambda e: e.duration):
                path = entry.realpath or os.path.realpath(entry.path)
                if os.path.splitext(path)[1].lower() in AUDIO_EXTS:
                    paths.add((entry.duration, path))
        elif station.get("network_type") == "web":
            from fs42_live.web_source import music_tracks  # (it imports this module)
            paths |= {(0, os.path.realpath(t)) for t in music_tracks(station)}
    with _lock:
        paths |= {(0, p) for p in _queued}
        _queued.clear()
    done = 0
    for _, path in sorted(paths):
        with _lock:
            if _measured(path) is not False or not os.path.exists(path):
                continue
        lufs = measure(path)
        with _lock:
            _load()[path] = {"size": os.path.getsize(path), "lufs": lufs}
            done += 1
            if done % 20 == 0:
                _save()
        _l.info(f"loudness: {os.path.basename(path)}: {lufs} LUFS")
    if done:
        with _lock:
            _save()
        _l.info(f"loudness: measured {done} files")


async def keep_measured():
    while True:
        try:
            await asyncio.to_thread(scan_new_files)
        except Exception:
            _l.exception("loudness: scan failed")
        await asyncio.sleep(CHECK_EVERY)
