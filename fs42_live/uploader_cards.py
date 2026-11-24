"""Cuts an uploader's own card off the start and end of a show.

Some uploaders put their own card on each video they post: Wink's Vault and The Game Show Vault
open a vault at the start and close it at the end, then hold a "Watch more / Subscribe" card. On a channel that's
not part of the show. Each card is kept in cards/ as a few tiny pictures (32x18, grey) of its
frames; here the first HEAD and last TAIL seconds of each show are compared with them.

A card at the start: the show starts after it, and after the tape's color bars, slate and
black if they come next. A card at the end: the show
ends where it starts (and any black just before it). Where it starts is kept in the fluid DB's
`play_starts` (the stretch before it is a cut, see play_cuts), where it ends in `play_ends`.
"""

import argparse
import datetime
import glob
import logging
import os
import sqlite3
import subprocess
import time
from contextlib import closing

import numpy as np

from fs42.catalog_api import CatalogAPI
from fs42.fluid_builder import FluidBuilder
from fs42.fluid_statements import FluidStatements
from fs42.station_manager import StationManager
from fs42_live.break_finder import _named, upcoming

_l = logging.getLogger("FS42LIVE")

FPS = 2
W, H = 32, 18
HEAD = 45  # seconds looked at, at the start
TAIL = 90  # and at the end
MATCH = 0.8  # how like a card's frame (correlation of the grey pictures) a frame of the card is
LEAST = 2  # frames in a row that make a card
FIRST = 10  # seconds: a card at the start begins this soon
LEAD = 20  # seconds after a start card in which the tape's bars, slate and black come
SLATE = 1.5  # seconds a slate is held at least
STILL = 1.5  # mean change (0-255 grey) from one frame to the next of a still picture
DARK = 12  # a frame no brighter than this on average is black (a bright line of tape noise or not)
KINDS = ("feature",)
REBUILD_EVERY = 3600
CARDS = os.path.join(os.path.dirname(__file__), "cards")


def cards():
    """All the cards' frames, one row each (float32, W*H grey)."""
    out = [np.load(p).reshape(-1, W * H) for p in sorted(glob.glob(os.path.join(CARDS, "*.npy")))]
    return np.concatenate(out).astype(np.float32) if out else np.zeros((0, W * H), np.float32)


def frames(path, head=None, tail=None):
    """(n, H, W, 3) uint8 frames FPS a second of the first `head` or last `tail` seconds."""
    span = ["-t", str(head)] if head else ["-sseof", str(-tail)]
    raw = subprocess.run(["nice", "-n", "19", "ffmpeg", "-v", "error", *span, "-i", path,
                          "-vf", f"fps={FPS},scale={W}:{H}", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8)[:len(raw) // (W * H * 3) * W * H * 3].reshape(-1, H, W, 3)


def grey(f):
    return f.astype(np.float32).mean(3).reshape(len(f), -1)


def is_card(f, ref):
    """For each frame, whether it's a card's frame."""
    if not len(f) or not len(ref):
        return np.zeros(len(f), bool)
    return (_z(grey(f)) @ _z(ref).T / (W * H)).max(1) > MATCH


def _z(g):
    """Each picture made even (mean 0, spread 1), so a brighter or darker copy of a card matches."""
    g = g - g.mean(1, keepdims=True)
    return g / np.maximum(g.std(1, keepdims=True), 1)


def is_dark(f):
    return f.reshape(len(f), -1).mean(1) <= DARK


def runs(m):
    out, i = [], 0
    while i < len(m):
        if m[i]:
            j = i
            while j < len(m) and m[j]:
                j += 1
            if j - i >= LEAST:
                out.append((i, j))
            i = j
        i += 1
    return out


def is_still(f):
    """For each frame, whether it's the same picture as the one before (a slate or a held card)."""
    g = grey(f)
    d = np.abs(np.diff(g, axis=0)).mean(1)
    return np.concatenate([[False], d < STILL])


def start_of(f, card):
    """Where the show starts in the first frames `f` (0 if no card opens it): after the card and
    the tape's lead that may follow it (color bars, a slate, black). The lead ends with black, or
    else with its slate, the longest still picture there."""
    r = [x for x in runs(card) if x[0] < FIRST * FPS]
    if not r:
        return 0.0
    i = r[-1][1]  # just after the card
    stop = min(len(f), i + LEAD * FPS)
    dark = np.nonzero(is_dark(f[i:stop]))[0]
    if len(dark):
        return (i + dark[-1] + 1) / FPS
    still = [x for x in runs(is_still(f[:stop])[i:]) if x[1] - x[0] >= SLATE * FPS]
    if still:
        return (i + max(still, key=lambda x: x[1] - x[0])[1]) / FPS
    return i / FPS


def end_of(f, card, looked_from):
    """Where the show ends, from the last frames `f` (from `looked_from` s); None if no card."""
    r = runs(card)
    if not r:
        return None
    i = r[0][0]
    dark = is_dark(f)
    while i > 0 and dark[i - 1]:
        i -= 1
    return looked_from + i / FPS


def look(path, duration, ref=None):
    """(where the show starts, where it ends) in a file `duration` long."""
    ref = cards() if ref is None else ref
    h = frames(path, head=min(HEAD, duration / 2))
    start = start_of(h, is_card(h, ref))
    tail = min(TAIL, duration / 2)
    t = frames(path, tail=tail)
    end = end_of(t, is_card(t, ref), duration - len(t) / FPS)
    if end is not None and end - start < duration / 2:
        end = None  # a card in the middle of a short file: leave it whole
    return start, end if end is not None else duration


def _looked(fluid):
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.init_db(db)
        return {p: d for p, d in db.execute("SELECT path, duration FROM play_starts")}


def store(fluid, path, start, end, whole):
    """Keeps where it starts, and where it ends if that's sooner than where it ended before."""
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.add_play_start(db, path, start, whole)
        if end < whole:
            before = FluidStatements.get_play_end(db, path, whole)
            if before is None or end < before:
                FluidStatements.add_play_end(db, path, end, whole)


def find_cards():
    """Looks at each show not looked at yet, what plays soonest first. Returns the stations to
    plan again and whether any are left."""
    from fs42_live.end_finder import _length, set_length
    ref = cards()
    if not len(ref):
        return [], False
    fluid = FluidBuilder()
    stations = [s for s in StationManager().stations if s.get("_has_catalog")]
    looked = _looked(fluid)
    todo = {}
    for station in stations:
        for entry in CatalogAPI.get_entries(station):
            if entry.content_type not in KINDS or getattr(entry, "media_type", "video") != "video":
                continue
            path = entry.realpath or os.path.realpath(entry.path)
            if path in looked:
                continue  # a replaced file is looked at again once its length is
            todo.setdefault(path, []).append(entry)
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
            start, end = look(path, whole, ref)
        except (OSError, subprocess.CalledProcessError, ValueError) as e:
            _l.warning(f"uploader cards: can't look at {path}: {e}")
            continue
        store(fluid, path, start, end, whole)
        if start > 0 or end < whole:
            cut += 1
            set_length(todo[path], fluid.get_play_length(path, whole))
            replan |= soon.get(path, (None, set()))[1]
            _l.info(f"uploader cards: {os.path.basename(path)}: starts at {start:.1f} s, ends {whole - end:.1f} s early")
    else:
        i = len(order)
    if cut:
        _l.info(f"uploader cards: {cut} shows play without an uploader's card")
    return _named(stations, replan), i < len(order)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    ref = cards()
    for path in a.paths:
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                 capture_output=True, text=True).stdout or 0)
        start, end = look(path, d, ref)
        print(f"{os.path.basename(path)}: {d:.1f} s, starts {start:.1f}, ends {end:.1f}", flush=True)


if __name__ == "__main__":
    main()
