"""Finds each episode's act breaks, so commercial breaks land where the network put them.

Upstream already cuts an episode at stored break points (the fluid DB's `break_points`, read
by LiquidBlock.make_plan), and `station_42.py --break_detect_dir` fills them in. But that
takes every black frame as a break, and dark night scenes are full of them. Here a break must
be black and quiet at once, the way a show faded out for the commercials. A half-hour often
has just one mid-show break plus fades after the cold open and before the tag, so breaks may
sit near either end. Episodes with too few (some run the music straight through, and rips with
the ads cut out have none) keep upstream's evenly spaced breaks.
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from collections import defaultdict
from contextlib import closing

from fs42.catalog_api import CatalogAPI
from fs42.fluid_builder import FluidBuilder
from fs42.fluid_statements import FluidStatements
from fs42.liquid_api import LiquidAPI
from fs42.media_processor import MediaProcessor
from fs42.station_manager import StationManager
from fs42_live import break_listener
from fs42_live.break_patterns import _apart, learn, look_closer, show_of
from fs42_live.catalog_watcher import follow_moves

_l = logging.getLogger("FS42LIVE")

SHORTEST_EPISODE = 600  # seconds; shorter clips are left alone
BLACK = 0.5  # seconds of black at a break...
SILENT = 0.3  # ...of which this much is also quiet
SILENCE_DB = -40  # dB; fades often stop short of true silence
SHORTEST_ACT = 180  # seconds between breaks
EDGE = 20  # seconds; how close to the start or end a break may be (after a cold open, before the tag)
FEWEST_BREAKS = 3  # fewer found isn't a pattern to trust...
LONGEST_ACT = 960  # ...unless they leave no act longer than this
PENDING = "runtime/break_finder_rebuild.json"  # stations whose schedules still need the new breaks
PEERS = "runtime/peer_breaks"  # our other boxes' breaks, one file each (Control Room's sharing fetches them)
LOOKED = "runtime/break_finder_looked.json"  # {episode: how its second look went} (second_look)
QUIET_AFTER = 3  # episodes of a show listened to with no cue before it isn't listened to any more
REBUILD_EVERY = 3600  # seconds of scanning episodes not on a schedule yet before the order is worked out again


def scan_new_episodes():
    """Finds breaks in each episode not scanned yet. Returns the stations to rebuild (those whose
    built schedule plays any of those episodes later on, so it was planned without them) and
    whether any episodes are still left. They're kept on disk until rebuilt, since the server
    may restart.

    An episode another box already scanned (the same file there: same name and size) takes its
    breaks from there. The rest go by what plays soonest on any channel's built schedule, so
    what's about to air has its breaks first; then the episodes not on the schedule yet, the
    channels with the fewest left first. It returns as soon as an episode on a built schedule has
    its breaks (scanned or taken), so that schedule is rebuilt before the episode airs; when
    another box's breaks come in, so they're taken before scanning on; and after an hour, so the
    order is worked out again from the schedules as they are.

    An episode with no clear breaks gets a second look (break_patterns, break_listener): near
    where its show's breaks usually fall, or, for a short show with no pattern yet, listening for
    the host's "we'll be right back". How each went is kept in LOOKED, so it's done once; one
    that found nothing is looked at again once its show has a pattern."""
    fluid = FluidBuilder()
    pending = _pending()
    stations = [s for s in StationManager().stations if s.get("_has_catalog")]
    for station in stations:
        follow_moves(station)
    stored, looked = _stored(fluid), _looked()
    patterns = learn(stored, {p for p, how in looked.items() if how in UNSURE})

    def needs(entry, path):
        if path not in stored:
            return True
        if stored[path]:
            return False
        if path not in looked:
            return True
        pattern = patterns.get(show_of(path))
        return looked[path] in ("none", "quiet") and pattern is not None and pattern.fits(entry.duration)

    todo = {s["network_name"]: [e for e in CatalogAPI.get_entries(s)
                                if e.content_type == "feature" and e.duration >= SHORTEST_EPISODE
                                and needs(e, e.realpath or os.path.realpath(e.path))]
            for s in stations}
    soon = upcoming(stations)
    shared = peer_breaks()
    left, seen, took = [], set(), 0
    for station in sorted(stations, key=lambda s: len(todo[s["network_name"]])):
        for entry in todo[station["network_name"]]:
            path = entry.realpath or os.path.realpath(entry.path)
            if path in seen:
                continue
            seen.add(path)
            segments = shared.get(_key(path)) if shared else None
            if not segments:
                if segments == [] and path not in stored:  # nothing clear there either: just the second look
                    store(fluid, path, [])
                    stored[path] = []
                left.append((entry, path))
                continue
            store(fluid, path, segments)
            stored[path] = segments
            took += 1
            pending |= soon.get(path, (None, set()))[1]
    if took:
        _l.info(f"break finder: {took} episodes' breaks came from another box with the same files")
        _save_pending(pending)
        if pending:  # what plays soon gets them now, before any scanning
            return _named(stations, pending), bool(left)
    never = datetime.datetime.max
    left.sort(key=lambda e: soon[e[1]][0] if e[1] in soon else never)  # stable: the rest keep the channels' order

    hearing = Hearing(looked)
    started, peers = time.monotonic(), _peers_stamp()
    for i, (entry, path) in enumerate(left):
        if i and (time.monotonic() - started >= REBUILD_EVERY or _peers_stamp() != peers):
            return _named(stations, pending), True
        try:
            points = []
            if path not in stored:
                points = find_breaks(path, entry.duration)
                if points:
                    _l.info(f"break finder: {entry.title}: breaks at {_clocks(points)}")
            if not points:
                points, how = second_look(path, entry.duration, patterns, hearing)
                if how is None:  # listening can't be done just now; it's tried again later
                    if path not in stored:
                        store(fluid, path, [])
                        stored[path] = []
                    continue
                looked[path] = how
                _save_looked(looked)
                _l.info(f"break finder: {entry.title}: no clear act breaks; {SAID[how]}"
                        + (f" {_clocks(points)}" if points else ""))
        except (OSError, subprocess.CalledProcessError) as e:
            _l.warning(f"break finder: can't scan {path}: {e}")
            continue
        store(fluid, path, as_segments(points, entry.duration))
        stored[path] = as_segments(points, entry.duration)
        playing = soon.get(path, (None, set()))[1] - pending
        if playing:
            pending |= playing
            _save_pending(pending)
            return _named(stations, pending), i + 1 < len(left)
    return _named(stations, pending), False


SAID = {"closer": "a closer look found", "usual": "at the show's usual times",
        "heard": "the host says they'll be right back at", "quiet": "keeping evenly spaced ones",
        "none": "keeping evenly spaced ones"}
UNSURE = ("closer", "usual")  # placed with a pattern's help, so they don't teach it


class Hearing:
    """Whether to listen to a show: listening works here, and the show hasn't been listened to
    QUIET_AFTER times without a cue (sitcoms don't say they'll be right back)."""

    def __init__(self, looked):
        self.works, self.broken = break_listener.available(), False
        self.quiet, self.heard = defaultdict(int), set()
        for path, how in looked.items():
            if how == "quiet":
                self.quiet[show_of(path)] += 1
            elif how == "heard":
                self.heard.add(show_of(path))

    def worth(self, path, duration):
        show = show_of(path)
        return (self.works and duration < break_listener.LONGEST
                and (show in self.heard or self.quiet[show] < QUIET_AFTER))

    def heard_it(self, path, any_cue):
        if any_cue:
            self.heard.add(show_of(path))
        else:
            self.quiet[show_of(path)] += 1


def second_look(path, duration, patterns, hearing):
    """(points, how) for an episode with no clear breaks; how is None when it should wait."""
    pattern = patterns.get(show_of(path))
    if pattern is not None and pattern.fits(duration):
        return look_closer(path, duration, pattern)
    if not hearing.worth(path, duration):
        return [], "none"
    if hearing.broken:
        return [], None
    cues = break_listener.listen(path)
    if cues is None:  # the rest wait too, till the next round
        hearing.broken = True
        return [], None
    points = _apart(sorted(t for t, _ in cues if EDGE <= t <= duration - EDGE))
    hearing.heard_it(path, bool(points))
    return points, "heard" if points else "quiet"


def _clocks(points):
    return ", ".join(clock(p) for p in points)


def _stored(fluid):
    """{path: segments} for every episode scanned ([] where no clear breaks were found)."""
    with closing(sqlite3.connect(fluid.db_path)) as db:
        return {path: json.loads(points) if points else [] for path, points in
                db.execute("SELECT path, points FROM break_points")}


def _looked():
    try:
        with open(LOOKED) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_looked(looked):
    with open(LOOKED + ".new", "w") as f:
        json.dump(looked, f)
    os.replace(LOOKED + ".new", LOOKED)


def _named(stations, names):
    return [s for s in stations if s["network_name"] in names]


def upcoming(stations):
    """{file: (when it next plays, the stations playing it)}, from each built schedule from now on."""
    now = datetime.datetime.now()
    soon = {}
    for station in stations:
        if not station.get("_has_schedule"):
            continue
        for block in LiquidAPI.get_blocks(station) or []:
            if block.start_time <= now:
                continue
            content = block.content if isinstance(block.content, list) else [block.content]
            for c in content:
                path = getattr(c, "realpath", None) or (getattr(c, "path", None) and os.path.realpath(c.path))
                if path:
                    when, names = soon.get(path, (block.start_time, set()))
                    soon[path] = (min(when, block.start_time), names | {station["network_name"]})
    return soon


def peer_breaks():
    """Our other boxes' breaks, {(file name, size): segments}, as Control Room fetched them."""
    shared = {}
    try:
        names = sorted(os.listdir(PEERS))
    except FileNotFoundError:
        return shared
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(PEERS, name)) as f:
                for file, size, segments in json.load(f).get("files", []):
                    shared[(file, size)] = segments
        except (OSError, ValueError, TypeError, AttributeError) as e:
            _l.warning(f"break finder: can't read {name}'s breaks: {e}")
    return shared


def _peers_stamp():
    """When another box's breaks last came in."""
    try:
        return max((e.stat().st_mtime for e in os.scandir(PEERS) if e.name.endswith(".json")), default=0)
    except OSError:
        return 0


def _key(path):
    try:
        return os.path.basename(path), os.path.getsize(path)
    except OSError:
        return None


def rebuilt(stations):
    _save_pending(_pending() - {s["network_name"] for s in stations})


def _pending():
    try:
        with open(PENDING) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def _save_pending(names):
    with open(PENDING, "w") as f:
        json.dump(sorted(names), f)


def find_breaks(path, duration):
    log = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-vf", f"blackdetect=d={BLACK}:pix_th=0.1:pic_th=0.95",
         "-af", f"silencedetect=n={SILENCE_DB}dB:d=0.1", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace", check=True).stderr
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
    points = [p for p in points if EDGE <= p <= duration - EDGE]

    # a teaser, then the titles a minute later, are both fades; keep the later one
    while len(points) > 1:
        edges = [0.0, *points, duration]
        lengths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
        shortest = min(range(1, len(lengths) - 1), key=lengths.__getitem__)
        if lengths[shortest] >= SHORTEST_ACT:
            break
        del points[shortest - 1]
    edges = [0.0, *points, duration]
    longest = max(edges[i + 1] - edges[i] for i in range(len(edges) - 1))
    return points if len(points) >= FEWEST_BREAKS or (points and longest <= LONGEST_ACT) else []


def as_segments(points, duration):
    # upstream's format: the acts between breaks
    if not points:
        return []
    edges = [0.0, *points, duration]
    segments = [{"chapter_start": edges[i], "chapter_end": edges[i + 1]} for i in range(len(edges) - 1)]
    return MediaProcessor.calc_black_segments(segments, duration)


def store(fluid, path, segments):
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.add_break_points(db, path, segments)


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02}"
