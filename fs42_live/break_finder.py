"""Finds each episode's act breaks, so commercial breaks land where the network put them.

Upstream already cuts an episode at stored break points (the fluid DB's `break_points`, read
by LiquidBlock.make_plan), and `station_42.py --break_detect_dir` fills them in. But that
takes every black frame as a break, and dark night scenes are full of them. Here a break must
be black and quiet at once, the way a show faded out for the commercials. Episodes with too
few of those (some run the music straight through) keep upstream's evenly spaced breaks.
"""

import datetime
import logging
import os
import re
import sqlite3
import subprocess
from contextlib import closing

from fs42.catalog_api import CatalogAPI
from fs42.fluid_builder import FluidBuilder
from fs42.fluid_statements import FluidStatements
from fs42.liquid_api import LiquidAPI
from fs42.media_processor import MediaProcessor
from fs42.station_manager import StationManager

_l = logging.getLogger("FS42LIVE")

SHORTEST_EPISODE = 600  # seconds; shorter clips are left alone
BLACK = 0.5  # seconds of black at a break...
SILENT = 0.3  # ...of which this much is also quiet
SILENCE_DB = -40  # dB; fades often stop short of true silence
SHORTEST_ACT = 180  # seconds; also how close to the start or end a break may be
FEWEST_BREAKS = 3  # fewer found isn't a pattern to trust


def scan_new_episodes():
    """Finds breaks in each episode not scanned yet. Returns the stations whose built
    schedule plays any of those episodes later on, so it was planned without them."""
    fluid = FluidBuilder()
    replan = []
    for station in StationManager().stations:
        if not station.get("_has_catalog"):
            continue
        scanned_now = set()
        for entry in CatalogAPI.get_entries(station):
            if entry.content_type != "feature" or entry.duration < SHORTEST_EPISODE:
                continue
            path = entry.realpath or os.path.realpath(entry.path)
            if is_scanned(fluid, path):
                continue
            try:
                points = find_breaks(path, entry.duration)
            except (OSError, subprocess.CalledProcessError) as e:
                _l.warning(f"break finder: can't scan {path}: {e}")
                continue
            if points:
                _l.info(f"break finder: {entry.title}: breaks at {', '.join(clock(p) for p in points)}")
            else:
                _l.info(f"break finder: {entry.title}: no clear act breaks; keeping evenly spaced ones")
            store(fluid, path, as_segments(points, entry.duration))
            scanned_now.add(path)
        if scanned_now and station.get("_has_schedule") and plays_later(station, scanned_now):
            replan.append(station)
    return replan


def find_breaks(path, duration):
    log = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-vf", f"blackdetect=d={BLACK}:pix_th=0.1:pic_th=0.95",
         "-af", f"silencedetect=n={SILENCE_DB}dB:d=0.1", "-f", "null", "-"],
        capture_output=True, text=True, check=True).stderr
    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    starts = [float(s) for s in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(e) for e in re.findall(r"silence_end: ([\d.]+)", log)] + [duration]
    silences = list(zip(starts, ends))

    points = []
    for b0, b1 in blacks:
        for s0, s1 in silences:
            lo, hi = max(b0, s0), min(b1, s1)
            if hi - lo >= SILENT:
                points.append((lo + hi) / 2)
                break
    points = [p for p in points if SHORTEST_ACT <= p <= duration - SHORTEST_ACT]

    # a teaser, then the titles a minute later, are both fades; keep the later one
    while len(points) > 1:
        edges = [0.0, *points, duration]
        lengths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
        shortest = min(range(1, len(lengths) - 1), key=lengths.__getitem__)
        if lengths[shortest] >= SHORTEST_ACT:
            break
        del points[shortest - 1]
    return points if len(points) >= FEWEST_BREAKS else []


def as_segments(points, duration):
    # upstream's format: the acts between breaks
    if not points:
        return []
    edges = [0.0, *points, duration]
    segments = [{"chapter_start": edges[i], "chapter_end": edges[i + 1]} for i in range(len(edges) - 1)]
    return MediaProcessor.calc_black_segments(segments, duration)


def is_scanned(fluid, path):
    # an episode without clear breaks is stored as [], which upstream's lookup can't tell apart
    with closing(sqlite3.connect(fluid.db_path)) as db:
        return db.execute("SELECT 1 FROM break_points WHERE path = ?", (path,)).fetchone() is not None


def store(fluid, path, segments):
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.add_break_points(db, path, segments)


def plays_later(station, paths):
    now = datetime.datetime.now()
    for block in LiquidAPI.get_blocks(station):
        if block.start_time > now:
            content = block.content if isinstance(block.content, list) else [block.content]
            if any(c is not None and c.realpath in paths for c in content):
                return True
    return False


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02}"
