"""On-demand: one episode or movie from the FieldStation42 catalog, for one viewer.

It plays through the same encode as the live channels (leveled volume, captions, the same
format), from a chosen point. There's no seeking within a stream: skipping ahead or back
starts a new one from the new point.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import threading
import time
import uuid
from asyncio.subprocess import DEVNULL, PIPE

from fs42.catalog import ShowCatalog
from fs42.catalog_api import CatalogAPI
from fs42.liquid_blocks import LiquidBlock
from fs42.liquid_manager import LiquidManager
from fs42.liquid_schedule import LiquidSchedule
from fs42_live import guide
from fs42_live.scheduled_source import LEAD, MIN_SEGMENT, NUDGE, ScheduledSource, Segment

PLANS = "runtime/ondemand_plans.json"
KEEP_PLANS = 100  # a device resuming a show in retro mode needs the same breaks as before

_durations = {}
_plans = None
_plans_lock = threading.Lock()


def now_showing(station):
    """The show on a channel now, to watch from its start: the feature playing, or, during a
    break, the one before it in the same block (else the one after). None if there isn't one."""
    if station["network_type"] == "web":
        return None
    try:
        pp = LiquidManager().get_play_point(station["network_name"], datetime.datetime.now() + NUDGE)
    except Exception:  # no schedule built, or it has run out
        return None
    if pp is None:
        return None
    features = [i for i, e in enumerate(pp.plan) if e.content_type == "feature" and not e.is_stream]
    before = [i for i in features if i <= pp.index]
    if not before and not features:
        return None
    entry = pp.plan[before[-1] if before else features[0]]
    name = os.path.splitext(os.path.basename(entry.path))[0]
    return {"channel": station["channel_number"], "file": entry.path, "name": name,
            "title": station.get("live_titles", {}).get(pp.block_title, pp.block_title) or name}


def _natural(text):
    """Sorts "Season 2" before "Season 10", and E9 before E10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text.lower())]


def catalog(station):
    """A channel's shows and movies in order, each with the folder it's in under its show (a
    season, say) as its group, and a label to list it by within that group."""
    if station["network_type"] == "web" or not station.get("content_dir"):
        return []
    items = []
    for e in CatalogAPI.get_entries(station):
        if e.content_type != "feature" or e.media_type != "video":
            continue
        parts = os.path.relpath(e.path, station["content_dir"]).split(os.sep)
        group = "/".join(parts[1:-1])
        name = os.path.splitext(parts[-1])[0]
        ep = guide.episode(name)
        if ep and not group:
            group = f"Season {ep[0]}"
        if e.duration:
            _durations[e.path] = e.duration  # (so its stream needn't ask ffprobe)
        items.append({"file": e.path, "name": name, "title": name, "group": group,
                      "label": ep[2] if ep and ep[2] else name, "episode": ep[1] if ep else None,
                      "duration": e.duration})
    return sorted(items, key=lambda i: _natural(i["file"]))


def catalogs():
    """The channels with something to watch on demand, and how much."""
    out = []
    for s in guide.streamable_stations():
        count = len(catalog(s))
        if count:
            out.append({"channel": s["channel_number"], "name": s["network_name"],
                        "display": guide.display_name(s), "count": count})
    return out


def allowed(station, path):
    """The path, if it's a video in the station's content folder (so the stream can't be
    pointed at any other file on the server), else None."""
    folder = os.path.abspath(station.get("content_dir", ""))
    full = os.path.abspath(path)
    if not station.get("content_dir") or os.path.commonpath([folder, full]) != folder or not os.path.isfile(full):
        return None
    return path


async def duration(path):
    if path not in _durations:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
            stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL,
        )
        out, _ = await proc.communicate()
        try:
            _durations[path] = float(out.strip())
        except ValueError:
            return None
    return _durations[path]


def retro(station, path):
    """Retro mode: the show as it would air in a slot of its own, cut at its breaks with the channel's
    commercials and bumpers in them, planned by the scheduler's own code. Kept, so a device can pick it
    up again later with the same breaks. None if it isn't in the catalog."""
    entry = CatalogAPI.get_by_path(station, path)
    if entry is None:
        return None
    planner = LiquidSchedule.__new__(LiquidSchedule)  # (its slot planning, without loading the channel's schedule)
    planner._l = logging.getLogger("Liquid")
    planner.conf, planner.catalog = station, ShowCatalog(station)
    break_info, strategy, increment = planner._break_info({}, entry.tag, entry.path)
    length = planner._calc_target_duration(entry.duration, increment, break_info, strategy)
    if not entry.duration or entry.duration < length / 2:
        return None  # (a music video, say: live, a slot strings many together; alone it'd be mostly breaks)
    when = datetime.datetime.now()
    block = LiquidBlock(entry, when, when + datetime.timedelta(seconds=length), entry.title, strategy, break_info)
    block.make_plan(planner.catalog)
    items = [{"file": e.path, "skip": e.skip, "duration": e.duration, "type": e.content_type}
             for e in block.plan if not e.is_stream and e.duration >= MIN_SEGMENT]
    last = max(i for i, item in enumerate(items) if item["type"] == "feature")
    items = items[:last + 1]  # (the slot's filler after the show: a recording would stop with the show)
    plan = {"id": uuid.uuid4().hex[:12], "file": path, "items": items,
            "duration": sum(i["duration"] for i in items), "made": time.time()}
    with _plans_lock:
        plans = _load_plans()
        plans[plan["id"]] = plan
        for old in sorted(plans.values(), key=lambda p: p["made"])[:-KEEP_PLANS]:
            del plans[old["id"]]
        tmp = PLANS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(plans, f)
        os.replace(tmp, PLANS)
    return plan


def _load_plans():
    global _plans
    if _plans is None:
        try:
            with open(PLANS) as f:
                _plans = json.load(f)
        except (OSError, ValueError):
            _plans = {}
    return _plans


def plan(plan_id):
    with _plans_lock:
        return _load_plans().get(plan_id)


def plan_segments(plan, start):
    """The plan from `start` seconds in."""
    segments, at = [], 0.0
    for item in plan["items"]:
        end = at + item["duration"]
        if end - start >= MIN_SEGMENT:
            offset = max(0.0, start - at)
            segments.append(Segment(item["file"], item["skip"] + offset, item["duration"] - offset))
        at = end
    return segments


class OnDemandSource(ScheduledSource):
    """Segments back to back (one file from a point, or a retro plan), through the live channels' encode."""

    def __init__(self, station_conf, segments):
        super().__init__(station_conf)
        self.segments = segments

    async def _plan(self, encodes):
        when, pts = time.monotonic(), 0.0
        for seg in self.segments:
            wait = when - time.monotonic() - LEAD  # (each encode starts just before its turn, as on live TV)
            if wait > 0:
                await asyncio.sleep(wait)
            out = asyncio.Queue()
            self._start(self._encode(seg, pts, out))
            await encodes.put(out)
            when += seg.duration
            pts += seg.duration
        await encodes.put(None)  # the stream ends with the show
