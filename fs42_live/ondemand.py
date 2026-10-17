"""On-demand: one episode or movie from the FieldStation42 catalog, for one viewer.

It plays through the same encode as the live channels (leveled volume, captions, the same
format), from a chosen point. There's no seeking within a stream: skipping ahead or back
starts a new one from the new point.
"""

import asyncio
import datetime
import os
from asyncio.subprocess import DEVNULL, PIPE

from fs42.liquid_manager import LiquidManager
from fs42_live.scheduled_source import NUDGE, ScheduledSource, Segment

_durations = {}


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


class OnDemandSource(ScheduledSource):
    """A single file from `start` to its end, through the live channels' encode."""

    def __init__(self, station_conf, path, start, length):
        super().__init__(station_conf)
        self.segment = Segment(path, start, length - start)

    async def _plan(self, encodes):
        out = asyncio.Queue()
        self._start(self._encode(self.segment, 0.0, out))
        await encodes.put(out)
        await encodes.put(None)  # the stream ends with the file
