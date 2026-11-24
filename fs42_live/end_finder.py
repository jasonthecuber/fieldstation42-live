"""Finds where each show and ad really ends, so it ends after its credits.

Many files run on after the show: seconds of black, a silent logo an uploader added (a circle
with their name on it), a silent card with a web address. Played to the end, the channel sits
on that before the next thing. Here the last 90 seconds of each file are looked at, and black
or silence that runs to the very end is cut off: the catalog's length for the file stops where
it starts, so the schedule plans it that long and plays the next thing then.

Not bumpers (a station ID may hold a silent picture on purpose). A silent stretch with a
picture longer than LONGEST_QUIET may be silent credits, and is kept; so is black with sound
under it that long. A file that would lose more than half isn't cut at all.
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing

from fs42.catalog_api import CatalogAPI
from fs42.catalog_io import CatalogIO
from fs42.fluid_builder import FluidBuilder
from fs42.fluid_statements import FluidStatements
from fs42.media_processor import MediaProcessor
from fs42.play_cuts import cut_total
from fs42.station_manager import StationManager
from fs42_live.break_finder import _named, upcoming

_l = logging.getLogger("FS42LIVE")

TAIL = 90  # seconds looked at, at the end of each file
BLACK = 0.2  # seconds of black that count
SILENCE_DB = -45  # dB
SILENT = 0.5  # seconds of quiet that count
TO_THE_END = 0.3  # a black or quiet stretch ending this close to the end runs to the end
LEAST = 1.0  # seconds; less than this at the end is left
TOGETHER = 1.5  # black starting this soon before the quiet does is part of the same ending
LONGEST_QUIET = 15  # seconds of a picture with no sound, or black with sound, still cut
KEEP = 0.5  # at least this much of a file plays
KINDS = ("feature", "commercial")
REBUILD_EVERY = 3600  # seconds of looking before the schedules playing what was cut are planned again


def find_ends():
    """Looks at each show and ad not looked at yet, what plays soonest first. Returns the stations
    to plan again (those whose built schedule plays a file that was cut, later on) and whether
    any are left. Also puts lengths already found into catalogs built without them."""
    fluid = FluidBuilder()
    stations = [s for s in StationManager().stations if s.get("_has_catalog")]
    found = _found(fluid)
    cuts = _cuts(fluid)
    todo, fixed = {}, 0
    for station in stations:
        for entry in CatalogAPI.get_entries(station):
            if entry.content_type not in KINDS or getattr(entry, "media_type", "video") != "video":
                continue
            path = entry.realpath or os.path.realpath(entry.path)
            if path in found:
                end, whole = found[path]
                skips = _skips(cuts, path, whole)
                plays = end - cut_total(skips, end)
                if abs(entry.duration - plays) < 1:
                    continue
                if (abs(entry.duration - whole) < 1 or abs(entry.duration - end) < 1
                        or abs(entry.duration - (whole - cut_total(skips, whole))) < 1):
                    set_length([entry], plays)
                    fixed += 1
                    continue
                # another file under the same name: looked at again
            todo.setdefault(path, []).append(entry)
    if fixed:
        _l.info(f"end finder: {fixed} catalog entries now stop where their files really end")
    if not todo:
        return [], False
    soon = upcoming(stations)
    never = datetime.datetime.max
    order = sorted(todo, key=lambda p: soon[p][0] if p in soon else never)
    replan, cut, started = set(), 0, time.monotonic()
    for i, path in enumerate(order):
        if i and time.monotonic() - started >= REBUILD_EVERY:
            break
        whole = _length(fluid, path)
        if not whole:
            continue
        try:
            end, why = find_end(path, whole)
        except (OSError, subprocess.CalledProcessError) as e:
            _l.warning(f"end finder: can't look at {path}: {e}")
            continue
        store(fluid, path, end, whole)
        if end < whole:
            cut += 1
            set_length(todo[path], end - cut_total(_skips(cuts, path, whole), end))
            replan |= soon.get(path, (None, set()))[1]
            _l.info(f"end finder: {os.path.basename(path)}: stops {whole - end:.1f} s early ({why})")
    else:
        i = len(order)
    if cut:
        _l.info(f"end finder: {cut} files stop where their picture and sound end")
    return _named(stations, replan), i < len(order)


def find_end(path, duration):
    """(where it ends, why) for a file `duration` long; (duration, "") if it runs to the end."""
    start = max(0.0, duration - TAIL)
    log = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-i", path,
         "-vf", f"blackdetect=d={BLACK}:pix_th=0.1", "-af", f"silencedetect=n={SILENCE_DB}dB:d={SILENT}",
         "-f", "null", "-"],
        capture_output=True, text=True, errors="replace", check=True).stderr
    blacks = [(float(a) + start, float(b) + start)
              for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    starts = [float(s) + start for s in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(e) + start for e in re.findall(r"silence_end: ([\d.]+)", log)]
    quiet = list(zip(starts, ends + [duration] * (len(starts) - len(ends))))
    return end_of(duration, blacks, quiet, start)


def end_of(duration, blacks, quiet, looked_from=0.0):
    """Where the black or quiet that runs to the end starts (see the module's notes)."""
    def at_end(spans):
        if spans and spans[-1][1] >= duration - TO_THE_END and duration - spans[-1][0] >= LEAST:
            return spans[-1][0]
        return None

    black, hush = at_end(blacks), at_end(quiet)
    if hush is not None and hush <= looked_from + SILENT:
        hush = None  # quiet all along (a silent film, or no sound at all): not an ending
    end, why = duration, ""
    if hush is not None:
        dark = [a for a, _ in blacks if hush - TOGETHER <= a <= hush + SILENT]
        if dark and (duration - hush <= LONGEST_QUIET or (black is not None and black <= hush + TOGETHER)):
            end, why = min(hush, min(dark)), "silent and black"
        elif duration - hush <= LONGEST_QUIET:
            end, why = hush, "silent"
    if black is not None and black < end and (
            duration - black <= LONGEST_QUIET or (hush is not None and hush <= black + TOGETHER)):
        end, why = black, "black" if not why else why
    if end < duration * KEEP:
        return duration, ""
    return end, why


def _found(fluid):
    """{path: (end, its length then)} for every file looked at."""
    with closing(sqlite3.connect(fluid.db_path)) as db:
        return {p: (e, d) for p, e, d in db.execute("SELECT path, end, duration FROM play_ends")}


def _cuts(fluid):
    """{path: (its length then, [stretches it skips])} for every file ad_finder looked at, and
    every file that starts after an uploader's card (uploader_cards)."""
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.init_db(db)
        out = {p: (d, [tuple(x) for x in json.loads(c)])
               for p, c, d in db.execute("SELECT path, cuts, duration FROM play_cuts")}
        for p, start, d in db.execute("SELECT path, start, duration FROM play_starts WHERE start > 0"):
            length, skips = out.get(p, (d, []))
            out[p] = (d, [(0.0, start)] + skips) if abs(length - d) < 1 else (d, [(0.0, start)])
        return out


def _skips(cuts, path, whole):
    """The stretches a file `whole` long skips; [] if none were stored for that length."""
    length, skips = cuts.get(path, (None, []))
    return skips if length is not None and abs(length - whole) < 1 else []


def _length(fluid, path):
    cached = fluid.check_file_cache(path)
    if cached and cached.duration:
        return cached.duration
    if not os.path.isfile(path):
        return None
    duration, _ = MediaProcessor._get_duration(path)
    return duration if duration and duration > 0 else None


def store(fluid, path, end, duration):
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.add_play_end(db, path, end, duration)


def set_length(entries, end):
    with CatalogIO()._get_connection() as db:
        for entry in entries:
            db.execute("UPDATE catalog_entries SET duration = ? WHERE id = ?", (end, entry.dbid))
