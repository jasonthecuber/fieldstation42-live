"""Evens out loudness between files, so a quiet movie and a loud ad play at about the same level.

Each file's loudness (EBU R128 integrated, in LUFS) is measured once, in the background, and the
encoder turns it up or down to `loudness_target` (fs42_live/settings.py). Turning up can push
peaks past full scale, so a limiter follows. A file not measured yet plays as it is, and is
queued, so a bump or slate that isn't in any catalog gets measured the first time it plays.
"""

import asyncio
import hashlib
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
LEVELLED = "runtime/levelled_music"  # web page channels' music, each track turned to the target
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


def music_playlist(tracks):
    """A web page channel's background music, levelled: the tracks to play and the -af chain. Its playlist
    is one input, so a filter can't turn each track by its own amount; each track gets a levelled copy made
    once instead. Until they're all made, the tracks as they are with one gain for the lot. (Levelling as it
    plays, with loudnorm, held up the screen capture: the page went choppy.)"""
    copies = []
    with _lock:
        for track in tracks:
            lufs = _measured(os.path.realpath(track))
            copy = lufs is not False and _copy_path(track, lufs)
            if not copy or not os.path.exists(copy):
                break
            copies.append(copy)
    if len(copies) < len(tracks):
        return tracks, _music_filter(tracks)
    return copies, "aresample=async=1"


def _copy_path(track, lufs):
    """Where a track's levelled copy goes: named for the track and its gain, so a new loudness target
    (or a changed track) makes a new copy."""
    gain = 0.0 if lufs is None or lufs < SILENT else _gain(lufs)
    path = os.path.realpath(track)
    digest = hashlib.sha1(f"{path}:{os.path.getsize(path)}".encode()).hexdigest()[:12]
    return os.path.join(os.path.abspath(LEVELLED), f"{digest}_{gain:+.1f}.m4a")  # (a playlist's relative paths are from its own folder)


def _make_copies(tracks):
    """Makes the levelled copies a channel's music is missing, and drops copies no longer wanted."""
    os.makedirs(LEVELLED, exist_ok=True)
    wanted = set()
    for track in tracks:
        with _lock:
            lufs = _measured(os.path.realpath(track))
        if lufs is False:
            continue
        copy = _copy_path(track, lufs)
        wanted.add(os.path.basename(copy))
        if os.path.exists(copy):
            continue
        gain = _gain(lufs) if lufs is not None and lufs >= SILENT else 0.0
        chain = f"volume={gain:.1f}dB" + (",alimiter=limit=0.89:level=false" if gain > 0 else "")
        tmp = copy + ".tmp.m4a"
        done = subprocess.run(
            ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", track, "-vn",
             "-af", chain, "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", tmp],
            capture_output=True, text=True)
        if done.returncode == 0:
            os.replace(tmp, copy)
            _l.info(f"loudness: levelled copy of {os.path.basename(track)} ({gain:+.1f} dB)")
        else:
            _l.warning(f"loudness: couldn't level {os.path.basename(track)}: {done.stderr.strip()[-200:]}")
    return wanted


def _music_filter(tracks):
    """One gain for all of a channel's music, from its tracks' middle loudness; a track not measured yet
    is queued."""
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


def _gain(lufs):
    return max(MOST_DOWN, min(MOST_UP, settings.get("loudness_target") - lufs))


def _levelled(lufs):
    gain = _gain(lufs)
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
    paths, music = set(), set()
    for station in StationManager().stations:
        if station.get("_has_catalog"):
            for entry in sorted(CatalogAPI.get_entries(station), key=lambda e: e.duration):
                path = entry.realpath or os.path.realpath(entry.path)
                if os.path.splitext(path)[1].lower() in AUDIO_EXTS:
                    paths.add((entry.duration, path))
        elif station.get("network_type") == "web":
            from fs42_live.web_source import music_tracks  # (it imports this module)
            music |= set(music_tracks(station))
    paths |= {(0, os.path.realpath(t)) for t in music}
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
    wanted = _make_copies(sorted(music))
    for name in set(os.listdir(LEVELLED)) - wanted:
        os.remove(os.path.join(LEVELLED, name))


async def keep_measured():
    while True:
        try:
            await asyncio.to_thread(scan_new_files)
        except Exception:
            _l.exception("loudness: scan failed")
        await asyncio.sleep(CHECK_EVERY)
